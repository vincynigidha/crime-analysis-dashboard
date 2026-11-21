"""
Crime-series insights (pure functions, no DB access -- app.py passes data in).

1. name_clusters()   -> gives every linked case group a readable series name
2. forecast_series() -> predicts the likely NEXT area(s) and date window of a series

These are decision-support estimates for patrol planning and case prioritisation.
They describe patterns in crimes, not individual people.
"""
from collections import Counter
from datetime import timedelta
from math import exp, radians, sin, cos, sqrt, atan2
from statistics import median


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * atan2(sqrt(a), sqrt(1 - a))


# ---------------------------------------------------------------- 1. naming

def name_clusters(clusters):
    """Adds cluster['series_name'], e.g. THEFT-RAMNAGAR-SEP26. Names are unique."""
    used = Counter()
    for c in clusters:
        area_counts = Counter(m["area_name"] for m in c["members"] if m["area_name"])
        top_area = area_counts.most_common(1)[0][0] if area_counts else "MULTI"
        area_code = "".join(ch for ch in top_area.upper() if ch.isalnum())[:8]
        cat_code = (c["category_name"] or "UNK").upper().replace(" ", "")[:8]
        base = f"{cat_code}-{area_code}-{c['date_start']:%b%y}".upper()
        used[base] += 1
        c["series_name"] = base if used[base] == 1 else f"{base}-{used[base]}"
    return clusters


# ---------------------------------------------------------------- 2. forecast

def forecast_series(cluster, locations, today, top_n=3, scale_km=1.5):
    """
    Predicts where and when a series is likely to strike next.

    WHERE: every known location is scored by how close it is to the series'
           incidents, with recent incidents weighing more (halves every 14 days).
           score(loc) = sum_i recency_i * exp(-distance(loc, incident_i) / scale_km)
    WHEN:  median gap between consecutive incidents, added to the last incident;
           also the most frequent weekday.
    Returns a dict, or None if there is no usable data.
    """
    members = sorted(cluster["members"], key=lambda m: m["reported_date"])
    if len(members) < 2 or not locations:
        return None
    last = members[-1]["reported_date"]

    scores = {}
    for loc in locations:
        if loc["latitude"] is None or loc["longitude"] is None:
            continue
        s = 0.0
        for m in members:
            age = (last - m["reported_date"]).days
            recency = 0.5 ** (age / 14.0)
            d = _km(float(loc["latitude"]), float(loc["longitude"]),
                    float(m["latitude"]), float(m["longitude"]))
            s += recency * exp(-d / scale_km)
        scores[loc["id"]] = (s, loc)

    ranked = sorted(scores.values(), key=lambda t: t[0], reverse=True)[:top_n]
    total = sum(s for s, _ in ranked) or 1.0
    places = [{"area": loc["area_name"], "district": loc.get("district"),
               "share": round(100 * s / total)} for s, loc in ranked]

    gaps = [(b["reported_date"] - a["reported_date"]).days for a, b in zip(members, members[1:])]
    gap = max(1, round(median(gaps))) if gaps else 7
    expected = last + timedelta(days=gap)
    overdue = expected < today
    if overdue:
        expected = today
    weekday = Counter(m["reported_date"].weekday() for m in members).most_common(1)[0][0]

    # Confidence grows with series size and with how regular the gaps are
    spread = (max(gaps) - min(gaps)) if gaps else 0
    conf = "High" if len(members) >= 5 and spread <= gap * 2 else "Medium" if len(members) >= 4 else "Low"

    return {
        "places": places,
        "expected_date": expected,
        "overdue": overdue,
        "avg_gap_days": gap,
        "weekday": WEEKDAYS[weekday],
        "confidence": conf,
    }
