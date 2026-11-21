"""
Generates a synthetic dataset/crime_dataset.csv with ~3 years of incident-level
crime records, so Module 7 (Prediction) has historical data to train on.

Run this once: python ml/generate_dataset.py

Replace dataset/crime_dataset.csv with real historical data later if you have it --
just keep the same columns (date, category, area) and re-run ml/train_model.py.
"""
import csv
import random
from datetime import date, timedelta

random.seed(42)

CATEGORIES = [
    "Theft", "Assault", "Fraud", "Burglary", "Robbery",
    "Homicide", "Cybercrime", "Vandalism", "Drug Offense", "Kidnapping",
]

AREAS = [
    "RS Puram", "Gandhipuram", "T. Nagar", "Anna Nagar", "Velachery",
    "Salem Junction", "Madurai Junction", "Trichy Junction",
    "Thanjavur Town", "Vellore Fort",
]

# Give each category a different "base rate" so some are naturally more common
CATEGORY_BASE_RATE = {
    "Theft": 0.14, "Assault": 0.10, "Fraud": 0.11, "Burglary": 0.09,
    "Robbery": 0.07, "Homicide": 0.02, "Cybercrime": 0.13,
    "Vandalism": 0.10, "Drug Offense": 0.10, "Kidnapping": 0.04,
    "Uncategorized": 0.10,
}

# Give each area a different "activity level" -- busier areas get more crimes
AREA_WEIGHT = {
    "RS Puram": 1.3, "Gandhipuram": 1.4, "T. Nagar": 1.6, "Anna Nagar": 1.2,
    "Velachery": 1.1, "Salem Junction": 1.0, "Madurai Junction": 1.1,
    "Trichy Junction": 0.9, "Thanjavur Town": 0.7, "Vellore Fort": 0.8,
}

START_DATE = date(2023, 1, 1)
END_DATE = date(2026, 6, 30)
TOTAL_DAYS = (END_DATE - START_DATE).days


def seasonal_multiplier(d: date) -> float:
    """Simple seasonal pattern: slightly more crime in Nov-Jan (festive/holiday season)."""
    if d.month in (11, 12, 1):
        return 1.3
    if d.month in (4, 5):
        return 1.1
    return 1.0


def yearly_growth_multiplier(d: date) -> float:
    """Mild upward trend year over year, so the model has a real trend to learn."""
    years_since_start = (d.year - START_DATE.year) + (d.month - 1) / 12
    return 1.0 + 0.05 * years_since_start


rows = []
incident_id = 1

current = START_DATE
while current <= END_DATE:
    base_daily_incidents = 3.5  # average incidents per day across the whole dataset
    multiplier = seasonal_multiplier(current) * yearly_growth_multiplier(current)
    expected_incidents = base_daily_incidents * multiplier

    num_incidents_today = max(0, round(random.gauss(expected_incidents, 1.5)))

    for _ in range(num_incidents_today):
        area = random.choices(AREAS, weights=[AREA_WEIGHT[a] for a in AREAS])[0]
        category = random.choices(CATEGORIES, weights=[CATEGORY_BASE_RATE[c] for c in CATEGORIES])[0]
        rows.append({
            "id": incident_id,
            "date": current.isoformat(),
            "category": category,
            "area": area,
        })
        incident_id += 1

    current += timedelta(days=1)

with open("dataset/crime_dataset.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["id", "date", "category", "area"])
    writer.writeheader()
    writer.writerows(rows)

print(f"Generated {len(rows)} synthetic crime records -> dataset/crime_dataset.csv")
print(f"Date range: {START_DATE} to {END_DATE}")
