from flask import Flask, render_template, request, redirect, url_for, session, flash
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import date, datetime
from db import init_db, get_db
import json
import os
import sys
import uuid
from math import radians, sin, cos, sqrt, atan2

sys.path.insert(0, os.path.dirname(__file__))
from ml import insights, forecast

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")   # required for session cookies
init_db(app)


def parse_date(text, default=None):
    """Accepts DD-MM-YYYY, DD/MM/YYYY or YYYY-MM-DD. Returns 'YYYY-MM-DD' for MySQL, or None if invalid."""
    text = (text or "").strip()
    if not text:
        return default
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


@app.template_filter("fmt_date")
def fmt_date(value, with_time=False):
    """Show any date / datetime / 'YYYY-MM-DD' string as DD-MM-YYYY (with optional HH:MM)."""
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    fmt = "%d-%m-%Y %H:%M" if with_time and isinstance(value, datetime) else "%d-%m-%Y"
    return value.strftime(fmt)


# ---------- Module 11: Case Linkage Detector ----------
# Flags groups of crime records that may be connected: same category,
# nearby location, and close together in time. Simple, explainable rules
# (not a black-box model) so every flagged link can be justified directly.

LINKAGE_RADIUS_KM = 3.0
LINKAGE_WINDOW_DAYS = 14
LINKAGE_MIN_CLUSTER_SIZE = 3


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lng points, in kilometers."""
    r = 6371.0
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def compute_case_linkages():
    """Returns (clusters, similar_counts).
    clusters: list of {category_name, size, date_start, date_end, areas, members}
              for groups of 3+ likely-connected cases, largest first.
    similar_counts: {crime_id: number of directly-linked cases} for list-page badges.
    """
    cur = get_db().cursor()
    cur.execute("""
        SELECT cr.id, cr.title, cr.status, cr.reported_date, cr.category_id, cc.name AS category_name,
               loc.area_name, loc.latitude, loc.longitude
        FROM crime_record cr
        JOIN location loc ON cr.location_id = loc.id
        LEFT JOIN crime_category cc ON cr.category_id = cc.id
        WHERE loc.latitude IS NOT NULL AND loc.longitude IS NOT NULL
        ORDER BY cr.reported_date
    """)
    records = cur.fetchall()
    cur.close()

    adjacency = {r["id"]: set() for r in records}
    n = len(records)

    for i in range(n):
        a = records[i]
        if a["category_id"] is None:
            continue
        for j in range(i + 1, n):
            b = records[j]
            if a["category_id"] != b["category_id"]:
                continue
            date_diff = abs((a["reported_date"] - b["reported_date"]).days)
            if date_diff > LINKAGE_WINDOW_DAYS:
                continue
            dist = _haversine_km(
                float(a["latitude"]), float(a["longitude"]),
                float(b["latitude"]), float(b["longitude"]),
            )
            if dist <= LINKAGE_RADIUS_KM:
                adjacency[a["id"]].add(b["id"])
                adjacency[b["id"]].add(a["id"])

    # Connected components via BFS -- groups of mutually-linked cases
    records_by_id = {r["id"]: r for r in records}
    visited = set()
    clusters = []

    for r in records:
        if r["id"] in visited or not adjacency[r["id"]]:
            visited.add(r["id"])
            continue

        component = set()
        stack = [r["id"]]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            visited.add(node)
            for neighbor in adjacency[node]:
                if neighbor not in component:
                    stack.append(neighbor)

        if len(component) >= LINKAGE_MIN_CLUSTER_SIZE:
            members = sorted((records_by_id[cid] for cid in component), key=lambda m: m["reported_date"])
            clusters.append({
                "category_name": members[0]["category_name"] or "Uncategorized",
                "size": len(members),
                "date_start": members[0]["reported_date"],
                "date_end": members[-1]["reported_date"],
                "areas": sorted({m["area_name"] for m in members if m["area_name"]}),
                "members": members,
            })

    clusters.sort(key=lambda c: c["size"], reverse=True)
    similar_counts = {r["id"]: len(adjacency[r["id"]]) for r in records}
    return clusters, similar_counts


def get_series_insights():
    """Linked case groups, each with a series name and a next place/date forecast."""
    clusters, similar_counts = compute_case_linkages()
    insights.name_clusters(clusters)
    cur = get_db().cursor()
    cur.execute("SELECT id, area_name, district, latitude, longitude FROM location")
    locations = cur.fetchall()
    cur.close()
    for c in clusters:
        c["forecast"] = insights.forecast_series(c, locations, date.today())
    return clusters, similar_counts


# ---------- Module 1: Auth helpers ----------

def login_required(f):
    """Decorator: redirect to login if no active session."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("portal_home"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        cur = get_db().cursor()
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        cur.close()

        if user and check_password_hash(user["password_hash"], password):
            # Module 1: Session Management
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            flash(f"Welcome back, {user['full_name'] or user['username']}!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# ---------- Module 2: Dashboard ----------

@app.route("/dashboard")
@login_required
def dashboard():
    cur = get_db().cursor()

    cur.execute("SELECT COUNT(*) AS total FROM crime_record")
    total_crimes = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS solved FROM crime_record WHERE status = 'Solved'")
    solved_crimes = cur.fetchone()["solved"]

    cur.execute("SELECT COUNT(*) AS pending FROM crime_record WHERE status = 'Pending'")
    pending_crimes = cur.fetchone()["pending"]

    cur.execute("SELECT COUNT(*) AS today FROM crime_record WHERE reported_date = %s", (date.today(),))
    todays_crimes = cur.fetchone()["today"]

    # Recent crime records (last 5)
    cur.execute("""
        SELECT cr.id, cr.title, cr.status, cr.reported_date, cc.name AS category
        FROM crime_record cr
        LEFT JOIN crime_category cc ON cr.category_id = cc.id
        ORDER BY cr.created_at DESC
        LIMIT 5
    """)
    recent_records = cur.fetchall()

    # Crime Category Chart data
    cur.execute("""
        SELECT cc.name AS category, COUNT(cr.id) AS count
        FROM crime_category cc
        LEFT JOIN crime_record cr ON cr.category_id = cc.id
        GROUP BY cc.id, cc.name
    """)
    category_chart = cur.fetchall()

    # Monthly Crime Chart data (current year)
    cur.execute("""
        SELECT MONTH(reported_date) AS month, COUNT(*) AS count
        FROM crime_record
        WHERE YEAR(reported_date) = YEAR(CURDATE())
        GROUP BY MONTH(reported_date)
        ORDER BY MONTH(reported_date)
    """)
    monthly_chart = cur.fetchall()

    cur.close()

    return render_template(
        "dashboard.html",
        total_crimes=total_crimes,
        solved_crimes=solved_crimes,
        pending_crimes=pending_crimes,
        todays_crimes=todays_crimes,
        recent_records=recent_records,
        category_chart=category_chart,
        monthly_chart=monthly_chart,
    )


# ---------- Module 9: Profile & Settings (view/change password only, for now) ----------

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    cur = get_db().cursor()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        new_password = request.form.get("new_password", "").strip()

        if new_password:
            password_hash = generate_password_hash(new_password)
            cur.execute(
                "UPDATE users SET full_name=%s, email=%s, password_hash=%s WHERE id=%s",
                (full_name, email, password_hash, session["user_id"]),
            )
        else:
            cur.execute(
                "UPDATE users SET full_name=%s, email=%s WHERE id=%s",
                (full_name, email, session["user_id"]),
            )
        get_db().commit()
        session["full_name"] = full_name
        flash("Profile updated.", "success")

    cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    user = cur.fetchone()
    cur.close()
    return render_template("profile.html", user=user)


# ---------- Module 3: Crime Category Management ----------

@app.route("/categories")
@login_required
def categories():
    cur = get_db().cursor()
    cur.execute("""
        SELECT cc.id, cc.name, cc.description,
               COUNT(cr.id) AS crime_count
        FROM crime_category cc
        LEFT JOIN crime_record cr ON cr.category_id = cc.id
        GROUP BY cc.id, cc.name, cc.description
        ORDER BY cc.name
    """)
    all_categories = cur.fetchall()
    cur.close()
    return render_template("category.html", categories=all_categories)


@app.route("/categories/add", methods=["POST"])
@login_required
def add_category():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("Category name is required.", "danger")
        return redirect(url_for("categories"))

    cur = get_db().cursor()
    cur.execute(
        "INSERT INTO crime_category (name, description) VALUES (%s, %s)",
        (name, description),
    )
    get_db().commit()
    cur.close()
    flash(f"Category '{name}' added.", "success")
    return redirect(url_for("categories"))


@app.route("/categories/edit/<int:category_id>", methods=["POST"])
@login_required
def edit_category(category_id):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("Category name is required.", "danger")
        return redirect(url_for("categories"))

    cur = get_db().cursor()
    cur.execute(
        "UPDATE crime_category SET name=%s, description=%s WHERE id=%s",
        (name, description, category_id),
    )
    get_db().commit()
    cur.close()
    flash(f"Category '{name}' updated.", "success")
    return redirect(url_for("categories"))


@app.route("/categories/delete/<int:category_id>", methods=["POST"])
@login_required
def delete_category(category_id):
    cur = get_db().cursor()
    # crime_record.category_id has ON DELETE SET NULL, so existing crime
    # records won't be deleted -- they'll just show "Uncategorized".
    cur.execute("DELETE FROM crime_category WHERE id=%s", (category_id,))
    get_db().commit()
    cur.close()
    flash("Category deleted.", "info")
    return redirect(url_for("categories"))


# ---------- Module 4: Crime Record Management (CRUD) ----------

@app.route("/crimes")
@login_required
def crimes():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    category_filter = request.args.get("category", "").strip()

    cur = get_db().cursor()

    query = """
        SELECT cr.id, cr.title, cr.description, cr.status, cr.reported_date,
               cc.id AS category_id, cc.name AS category_name,
               loc.id AS location_id, loc.area_name
        FROM crime_record cr
        LEFT JOIN crime_category cc ON cr.category_id = cc.id
        LEFT JOIN location loc ON cr.location_id = loc.id
        WHERE 1=1
    """
    params = []

    if search:
        query += " AND (cr.title LIKE %s OR cr.description LIKE %s)"
        like_term = f"%{search}%"
        params.extend([like_term, like_term])

    if status_filter:
        query += " AND cr.status = %s"
        params.append(status_filter)

    if category_filter:
        query += " AND cr.category_id = %s"
        params.append(category_filter)

    query += " ORDER BY cr.reported_date DESC, cr.id DESC"

    cur.execute(query, params)
    records = cur.fetchall()

    cur.execute("SELECT id, name FROM crime_category ORDER BY name")
    all_categories = cur.fetchall()

    cur.execute("SELECT id, area_name FROM location ORDER BY area_name")
    all_locations = cur.fetchall()

    cur.close()

    clusters, similar_counts = get_series_insights()
    series_by_crime = {m["id"]: c["series_name"] for c in clusters for m in c["members"]}

    return render_template(
        "crime.html",
        records=records,
        categories=all_categories,
        locations=all_locations,
        search=search,
        status_filter=status_filter,
        category_filter=category_filter,
        today=date.today().isoformat(),
        similar_counts=similar_counts,
        series_by_crime=series_by_crime,
    )


@app.route("/crimes/add", methods=["POST"])
@login_required
def add_crime():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category_id = request.form.get("category_id") or None
    location_id = request.form.get("location_id") or None
    status = request.form.get("status", "Pending")
    reported_date = parse_date(request.form.get("reported_date"), date.today().isoformat())
    if reported_date is None:
        flash("Invalid date. Please use DD-MM-YYYY (for example 25-10-2026).", "danger")
        return redirect(url_for("crimes"))

    if not title:
        flash("Crime title is required.", "danger")
        return redirect(url_for("crimes"))

    cur = get_db().cursor()
    cur.execute(
        """INSERT INTO crime_record (title, description, category_id, location_id, status, reported_date)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (title, description, category_id, location_id, status, reported_date),
    )
    get_db().commit()
    cur.close()
    flash(f"Crime record '{title}' added.", "success")
    return redirect(url_for("crimes"))


@app.route("/crimes/edit/<int:crime_id>", methods=["POST"])
@login_required
def edit_crime(crime_id):
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category_id = request.form.get("category_id") or None
    location_id = request.form.get("location_id") or None
    status = request.form.get("status", "Pending")
    reported_date = parse_date(request.form.get("reported_date"), date.today().isoformat())
    if reported_date is None:
        flash("Invalid date. Please use DD-MM-YYYY (for example 25-10-2026).", "danger")
        return redirect(url_for("crimes"))

    if not title:
        flash("Crime title is required.", "danger")
        return redirect(url_for("crimes"))

    cur = get_db().cursor()
    cur.execute(
        """UPDATE crime_record
           SET title=%s, description=%s, category_id=%s, location_id=%s, status=%s, reported_date=%s
           WHERE id=%s""",
        (title, description, category_id, location_id, status, reported_date, crime_id),
    )
    get_db().commit()
    cur.close()
    flash(f"Crime record '{title}' updated.", "success")
    return redirect(url_for("crimes"))


@app.route("/crimes/delete/<int:crime_id>", methods=["POST"])
@login_required
def delete_crime(crime_id):
    cur = get_db().cursor()
    cur.execute("DELETE FROM crime_record WHERE id=%s", (crime_id,))
    get_db().commit()
    cur.close()
    flash("Crime record deleted.", "info")
    return redirect(url_for("crimes"))


# ---------- Module 6: Crime Location Analysis ----------

@app.route("/locations")
@login_required
def locations():
    cur = get_db().cursor()

    # Area Wise Crime: crime count per location, for table + map + heat map
    cur.execute("""
        SELECT loc.id, loc.area_name, loc.district, loc.latitude, loc.longitude,
               COUNT(cr.id) AS crime_count
        FROM location loc
        LEFT JOIN crime_record cr ON cr.location_id = loc.id
        GROUP BY loc.id, loc.area_name, loc.district, loc.latitude, loc.longitude
        ORDER BY crime_count DESC
    """)
    area_data = cur.fetchall()
    cur.close()

    # High Crime Area: top of the same list (non-zero only)
    high_crime_areas = [a for a in area_data if a["crime_count"] > 0][:3]

    return render_template(
        "location.html",
        areas=area_data,
        high_crime_areas=high_crime_areas,
    )


@app.route("/locations/add", methods=["POST"])
@login_required
def add_location():
    area_name = request.form.get("area_name", "").strip()
    district = request.form.get("district", "").strip()
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()

    if not area_name or not latitude or not longitude:
        flash("Area name, latitude, and longitude are all required.", "danger")
        return redirect(url_for("locations"))

    try:
        lat_val = float(latitude)
        lng_val = float(longitude)
    except ValueError:
        flash("Latitude and longitude must be numbers.", "danger")
        return redirect(url_for("locations"))

    cur = get_db().cursor()
    cur.execute(
        "INSERT INTO location (area_name, district, latitude, longitude) VALUES (%s, %s, %s, %s)",
        (area_name, district, lat_val, lng_val),
    )
    get_db().commit()
    cur.close()
    flash(f"Location '{area_name}' added.", "success")
    return redirect(url_for("locations"))


@app.route("/locations/delete/<int:location_id>", methods=["POST"])
@login_required
def delete_location(location_id):
    cur = get_db().cursor()
    # crime_record.location_id has ON DELETE SET NULL, so existing crime
    # records won't be deleted -- they'll just show no location.
    cur.execute("DELETE FROM location WHERE id=%s", (location_id,))
    get_db().commit()
    cur.close()
    flash("Location deleted.", "info")
    return redirect(url_for("locations"))


# ---------- Module 5: Crime Trend Analysis ----------

@app.route("/trends")
@login_required
def trends():
    period = request.args.get("period", "monthly")
    if period not in ("daily", "weekly", "monthly", "yearly", "category"):
        period = "monthly"

    cur = get_db().cursor()
    labels = []
    counts = []
    chart_type = "line"
    previous_total = None  # total for the prior comparable window, for a trend delta

    if period == "daily":
        # Last 30 days
        cur.execute("""
            SELECT DATE(reported_date) AS d, COUNT(*) AS c
            FROM crime_record
            WHERE reported_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY DATE(reported_date)
            ORDER BY d
        """)
        for row in cur.fetchall():
            labels.append(row["d"].strftime("%d-%m-%Y"))
            counts.append(row["c"])

        cur.execute("""
            SELECT COUNT(*) AS c FROM crime_record
            WHERE reported_date >= DATE_SUB(CURDATE(), INTERVAL 60 DAY)
              AND reported_date < DATE_SUB(CURDATE(), INTERVAL 30 DAY)
        """)
        previous_total = cur.fetchone()["c"]

    elif period == "weekly":
        # Last 12 weeks
        cur.execute("""
            SELECT YEARWEEK(reported_date, 3) AS yw, MIN(reported_date) AS week_start, COUNT(*) AS c
            FROM crime_record
            WHERE reported_date >= DATE_SUB(CURDATE(), INTERVAL 12 WEEK)
            GROUP BY YEARWEEK(reported_date, 3)
            ORDER BY yw
        """)
        for row in cur.fetchall():
            labels.append("Week of " + row["week_start"].strftime("%d-%m-%Y"))
            counts.append(row["c"])

        cur.execute("""
            SELECT COUNT(*) AS c FROM crime_record
            WHERE reported_date >= DATE_SUB(CURDATE(), INTERVAL 24 WEEK)
              AND reported_date < DATE_SUB(CURDATE(), INTERVAL 12 WEEK)
        """)
        previous_total = cur.fetchone()["c"]

    elif period == "monthly":
        # Last 12 months
        cur.execute("""
            SELECT YEAR(reported_date) AS y, MONTH(reported_date) AS m, COUNT(*) AS c
            FROM crime_record
            WHERE reported_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
            GROUP BY YEAR(reported_date), MONTH(reported_date)
            ORDER BY y, m
        """)
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        for row in cur.fetchall():
            labels.append(f"{row['m']:02d}-{row['y']}")
            counts.append(row["c"])

        cur.execute("""
            SELECT COUNT(*) AS c FROM crime_record
            WHERE reported_date >= DATE_SUB(CURDATE(), INTERVAL 24 MONTH)
              AND reported_date < DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
        """)
        previous_total = cur.fetchone()["c"]

    elif period == "yearly":
        cur.execute("""
            SELECT YEAR(reported_date) AS y, COUNT(*) AS c
            FROM crime_record
            GROUP BY YEAR(reported_date)
            ORDER BY y
        """)
        rows = cur.fetchall()
        for row in rows:
            labels.append(str(row["y"]))
            counts.append(row["c"])
        # Previous period = the second-most-recent year in the data, if any
        if len(rows) >= 2:
            previous_total = rows[-2]["c"]

    elif period == "category":
        chart_type = "bar"
        cur.execute("""
            SELECT cc.name, COUNT(cr.id) AS c
            FROM crime_category cc
            LEFT JOIN crime_record cr ON cr.category_id = cc.id
            GROUP BY cc.id, cc.name
            ORDER BY c DESC
        """)
        for row in cur.fetchall():
            labels.append(row["name"])
            counts.append(row["c"])

    total_in_period = sum(counts)
    cur.close()

    delta = None
    delta_pct = None
    if previous_total is not None:
        delta = total_in_period - previous_total
        delta_pct = round((delta / previous_total) * 100, 1) if previous_total > 0 else None

    # Breakdown table: label + count + share of total, for the data table under the chart
    breakdown = []
    for label, count in zip(labels, counts):
        share = round((count / total_in_period) * 100, 1) if total_in_period > 0 else 0
        breakdown.append({"label": label, "count": count, "share": share})
    breakdown.sort(key=lambda row: row["count"], reverse=True)

    return render_template(
        "trend.html",
        period=period,
        labels=labels,
        counts=counts,
        chart_type=chart_type,
        total_in_period=total_in_period,
        previous_total=previous_total,
        delta=delta,
        delta_pct=delta_pct,
        breakdown=breakdown,
    )


# ---------- Module 8: Reports ----------

def _reports_query_and_filters():
    """Shared logic: build the SQL + params + display filters for the current report request.
    Used by both the reports page and the CSV export so they always match."""
    period = request.args.get("period", "daily")
    if period not in ("daily", "monthly", "yearly"):
        period = "daily"

    today = date.today()
    query = """
        SELECT cr.id, cr.title, cr.description, cr.status, cr.reported_date,
               cc.name AS category_name, loc.area_name, loc.district
        FROM crime_record cr
        LEFT JOIN crime_category cc ON cr.category_id = cc.id
        LEFT JOIN location loc ON cr.location_id = loc.id
        WHERE 1=1
    """
    params = []
    filters = {"period": period}

    if period == "daily":
        report_date = parse_date(request.args.get("date"), today.isoformat()) or today.isoformat()
        query += " AND cr.reported_date = %s"
        params.append(report_date)
        filters["date"] = report_date

    elif period == "monthly":
        year = request.args.get("year", str(today.year))
        month = request.args.get("month", str(today.month))
        query += " AND YEAR(cr.reported_date) = %s AND MONTH(cr.reported_date) = %s"
        params.extend([year, month])
        filters["year"] = year
        filters["month"] = month

    elif period == "yearly":
        year = request.args.get("year", str(today.year))
        query += " AND YEAR(cr.reported_date) = %s"
        params.append(year)
        filters["year"] = year

    query += " ORDER BY cr.reported_date DESC, cr.id DESC"
    return query, params, filters


@app.route("/reports")
@login_required
def reports():
    query, params, filters = _reports_query_and_filters()

    cur = get_db().cursor()
    cur.execute(query, params)
    records = cur.fetchall()
    cur.close()

    return render_template(
        "reports.html",
        records=records,
        filters=filters,
        today=date.today().isoformat(),
    )


@app.route("/reports/export")
@login_required
def export_report():
    import csv
    import io
    from flask import Response

    query, params, filters = _reports_query_and_filters()

    cur = get_db().cursor()
    cur.execute(query, params)
    records = cur.fetchall()
    cur.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Title", "Description", "Category", "Area", "District", "Status", "Reported Date"])
    for r in records:
        writer.writerow([
            r["id"], r["title"], r["description"] or "",
            r["category_name"] or "", r["area_name"] or "", r["district"] or "",
            r["status"], r["reported_date"].strftime("%d-%m-%Y"),
        ])

    filename = f"crime_report_{filters['period']}_{date.today().strftime('%d-%m-%Y')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------- Module 7: Crime Prediction ----------

@app.route("/prediction")
@login_required
def prediction():
    """Forecast for the next 30 days, built from Crime Records + Case Linkage only."""
    cur = get_db().cursor()
    cur.execute("""
        SELECT cr.id, cr.reported_date, loc.area_name, cc.name AS category_name
        FROM crime_record cr
        LEFT JOIN location loc ON cr.location_id = loc.id
        LEFT JOIN crime_category cc ON cr.category_id = cc.id
    """)
    all_rows = cur.fetchall()
    cur.execute("SELECT area_name FROM location ORDER BY area_name")
    areas = [r["area_name"] for r in cur.fetchall()]
    cur.execute("SELECT name FROM crime_category ORDER BY name")
    categories = [r["name"] for r in cur.fetchall()]
    cur.close()

    usable = [r for r in all_rows if r["area_name"] and r["category_name"]]
    today = date.today()
    clusters, _ = get_series_insights()
    cells = forecast.predict_cells(usable, clusters, today)
    test = forecast.backtest(usable, today)

    selected_area = request.args.get("area", "").strip()
    selected_category = request.args.get("category", "").strip()
    result = None
    if selected_area and selected_category:
        result = next((c for c in cells
                       if c["area"] == selected_area and c["category"] == selected_category), None)
        if result is None:
            result = {"area": selected_area, "category": selected_category, "past_cases": 0,
                      "last_case": None, "expected": 0.0, "risk": "Low", "series_name": None,
                      "used_series": False, "record_expected": 0.0, "series_expected": None}

    dates = [r["reported_date"] for r in usable]
    return render_template(
        "prediction.html",
        total_cases=len(all_rows),
        used_cases=len(usable),
        series_count=len(clusters),
        series_case_count=sum(c["size"] for c in clusters),
        date_from=min(dates) if dates else None,
        date_to=max(dates) if dates else None,
        cells=cells[:15],
        backtest=test,
        areas=areas,
        categories=categories,
        selected_area=selected_area,
        selected_category=selected_category,
        result=result,
        horizon=forecast.HORIZON_DAYS,
        today=today,
    )


# ---------- Module 10: Citizen Portal (public, no login required) ----------

def _generate_reference_id():
    """Non-sequential, non-guessable reference ID so citizens can't enumerate
    other people's reports just by trying nearby IDs."""
    year = date.today().year
    random_part = uuid.uuid4().hex[:8].upper()
    return f"CR-{year}-{random_part}"


@app.route("/portal")
def portal_home():
    return render_template("portal_home.html")


@app.route("/portal/report", methods=["GET", "POST"])
def report_crime_public():
    cur = get_db().cursor()
    cur.execute("SELECT id, name FROM crime_category ORDER BY name")
    categories = cur.fetchall()

    if request.method == "POST":
        category_id = request.form.get("category_id") or None
        description = request.form.get("description", "").strip()
        area_name = request.form.get("area_name", "").strip()
        latitude = request.form.get("latitude") or None
        longitude = request.form.get("longitude") or None
        incident_date = parse_date(request.form.get("incident_date"), date.today().isoformat())
        reporter_name = request.form.get("reporter_name", "").strip()
        reporter_phone = request.form.get("reporter_phone", "").strip()
        reporter_email = request.form.get("reporter_email", "").strip()

        if incident_date is None or incident_date > date.today().isoformat():
            flash("Please enter a valid incident date as DD-MM-YYYY (not in the future).", "danger")
            cur.close()
            return render_template("report_crime.html", categories=categories, today=date.today().isoformat())

        if not description:
            flash("Please describe what happened.", "danger")
            cur.close()
            return render_template("report_crime.html", categories=categories, today=date.today().isoformat())

        reference_id = _generate_reference_id()

        cur.execute(
            """INSERT INTO citizen_report
               (reference_id, category_id, description, area_name, latitude, longitude,
                incident_date, reporter_name, reporter_phone, reporter_email, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending Review')""",
            (reference_id, category_id, description, area_name, latitude, longitude,
             incident_date, reporter_name, reporter_phone, reporter_email),
        )
        get_db().commit()
        cur.close()

        return render_template("report_confirmation.html", reference_id=reference_id)

    cur.close()
    return render_template("report_crime.html", categories=categories, today=date.today().isoformat())


@app.route("/portal/track", methods=["GET", "POST"])
def track_status():
    result = None
    searched = False

    if request.method == "POST":
        searched = True
        reference_id = request.form.get("reference_id", "").strip().upper()

        cur = get_db().cursor()
        cur.execute("""
            SELECT cr.reference_id, cr.description, cr.incident_date, cr.status AS report_status,
                   cr.created_at, cc.name AS category_name, crec.status AS crime_status
            FROM citizen_report cr
            LEFT JOIN crime_category cc ON cr.category_id = cc.id
            LEFT JOIN crime_record crec ON cr.linked_crime_id = crec.id
            WHERE cr.reference_id = %s
        """, (reference_id,))
        result = cur.fetchone()
        cur.close()

        if result:
            # Derive a citizen-friendly status: once verified, mirror the
            # linked crime record's real investigation status.
            if result["report_status"] == "Verified":
                result["display_status"] = "Resolved" if result["crime_status"] == "Solved" else "Under Investigation"
            else:
                result["display_status"] = result["report_status"]

    return render_template("track_status.html", result=result, searched=searched)


@app.route("/portal/safety")
def safety_info():
    return render_template("safety_info.html")


@app.route("/portal/stats")
def public_stats():
    cur = get_db().cursor()

    cur.execute("""
        SELECT cc.name, COUNT(cr.id) AS c
        FROM crime_category cc
        LEFT JOIN crime_record cr ON cr.category_id = cc.id
        GROUP BY cc.id, cc.name
        ORDER BY c DESC
    """)
    category_data = cur.fetchall()

    cur.execute("""
        SELECT loc.area_name, COUNT(cr.id) AS c
        FROM location loc
        LEFT JOIN crime_record cr ON cr.location_id = loc.id
        GROUP BY loc.id, loc.area_name
        HAVING c > 0
        ORDER BY c DESC
        LIMIT 5
    """)
    top_areas = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS total FROM crime_record")
    total_crimes = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS solved FROM crime_record WHERE status = 'Solved'")
    solved_crimes = cur.fetchone()["solved"]

    cur.close()

    return render_template(
        "public_stats.html",
        category_data=category_data,
        top_areas=top_areas,
        total_crimes=total_crimes,
        solved_crimes=solved_crimes,
    )


# ---------- Module 10 (admin side): Citizen Report Review Queue ----------

@app.route("/citizen-reports")
@login_required
def citizen_reports():
    cur = get_db().cursor()
    cur.execute("""
        SELECT cr.id, cr.reference_id, cr.description, cr.area_name, cr.latitude, cr.longitude,
               cr.incident_date, cr.reporter_name, cr.reporter_phone, cr.reporter_email,
               cr.status, cr.created_at, cc.name AS category_name
        FROM citizen_report cr
        LEFT JOIN crime_category cc ON cr.category_id = cc.id
        ORDER BY FIELD(cr.status, 'Pending Review', 'Verified', 'Rejected'), cr.created_at DESC
    """)
    reports = cur.fetchall()
    cur.close()
    return render_template("citizen_reports.html", reports=reports)


@app.route("/citizen-reports/approve/<int:report_id>", methods=["POST"])
@login_required
def approve_citizen_report(report_id):
    cur = get_db().cursor()
    cur.execute("SELECT * FROM citizen_report WHERE id = %s", (report_id,))
    report = cur.fetchone()

    if not report:
        flash("Report not found.", "danger")
        cur.close()
        return redirect(url_for("citizen_reports"))

    # Create a location entry from the citizen-supplied coordinates, if given
    location_id = None
    if report["latitude"] and report["longitude"]:
        cur.execute(
            "INSERT INTO location (area_name, latitude, longitude) VALUES (%s, %s, %s)",
            (report["area_name"] or "Citizen-reported location", report["latitude"], report["longitude"]),
        )
        get_db().commit()
        location_id = cur.lastrowid

    title = f"Citizen Report: {report['description'][:60]}"
    cur.execute(
        """INSERT INTO crime_record (title, description, category_id, location_id, status, reported_date)
           VALUES (%s, %s, %s, %s, 'Pending', %s)""",
        (title, report["description"], report["category_id"], location_id, report["incident_date"]),
    )
    get_db().commit()
    new_crime_id = cur.lastrowid

    cur.execute(
        "UPDATE citizen_report SET status = 'Verified', linked_crime_id = %s WHERE id = %s",
        (new_crime_id, report_id),
    )
    get_db().commit()
    cur.close()

    flash(f"Report {report['reference_id']} approved and added as crime record #{new_crime_id}.", "success")
    return redirect(url_for("citizen_reports"))


@app.route("/citizen-reports/reject/<int:report_id>", methods=["POST"])
@login_required
def reject_citizen_report(report_id):
    cur = get_db().cursor()
    cur.execute("UPDATE citizen_report SET status = 'Rejected' WHERE id = %s", (report_id,))
    get_db().commit()
    cur.close()
    flash("Report rejected.", "info")
    return redirect(url_for("citizen_reports"))


# ---------- Module 11 (page): Case Linkage ----------

@app.route("/case-linkage")
@login_required
def case_linkage():
    clusters, _ = get_series_insights()
    return render_template(
        "case_linkage.html",
        clusters=clusters,
        radius_km=LINKAGE_RADIUS_KM,
        window_days=LINKAGE_WINDOW_DAYS,
    )


if __name__ == "__main__":
    app.run(debug=True)
