"""
Trains a model that predicts next month's crime count, per (area, category).

Run this once initially, and again anytime you want to retrain on updated data:
    python ml/train_model.py

Or trigger it from the web UI -- Module 7 -> "Train Model" button, which calls
the train() function below via app.py.
"""
import json
import os

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import LabelEncoder

DATASET_PATH = os.path.join(os.path.dirname(__file__), "..", "dataset", "crime_dataset.csv")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.pkl")
ENCODERS_PATH = os.path.join(os.path.dirname(__file__), "encoders.pkl")
LAST_COUNTS_PATH = os.path.join(os.path.dirname(__file__), "last_counts.pkl")
METRICS_PATH = os.path.join(os.path.dirname(__file__), "metrics.json")


def _build_monthly_features(df: pd.DataFrame) -> pd.DataFrame:
    """Turn incident-level rows into one row per (area, category, year, month) with a count,
    plus a lag_1 feature (previous month's count for that same area+category)."""
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month

    monthly = (
        df.groupby(["area", "category", "year", "month"])
        .size()
        .reset_index(name="count")
        .sort_values(["area", "category", "year", "month"])
    )

    # Build a continuous month index so we can shift() correctly even across year boundaries
    monthly["month_index"] = monthly["year"] * 12 + monthly["month"]

    # Ensure every (area, category) has a fully continuous monthly series (fill gaps with 0)
    filled_groups = []
    for (area, category), group in monthly.groupby(["area", "category"]):
        full_idx = range(group["month_index"].min(), group["month_index"].max() + 1)
        group = group.set_index("month_index").reindex(full_idx)
        group["area"] = area
        group["category"] = category
        group["count"] = group["count"].fillna(0)
        idx_values = group.index.to_numpy()
        month_values = idx_values % 12
        month_values = pd.Series(month_values).replace(0, 12).to_numpy()
        group["month"] = month_values
        group["year"] = (idx_values - month_values) // 12
        group = group.reset_index().rename(columns={"index": "month_index"})
        filled_groups.append(group)
    monthly = pd.concat(filled_groups, ignore_index=True)

    monthly["lag_1"] = monthly.groupby(["area", "category"])["count"].shift(1)
    monthly = monthly.dropna(subset=["lag_1"]).reset_index(drop=True)

    return monthly


def train():
    """Trains the model and saves model/encoders/metrics to disk.
    Returns a dict of metrics for display in the UI."""
    df = pd.read_csv(DATASET_PATH)
    monthly = _build_monthly_features(df)

    area_encoder = LabelEncoder()
    category_encoder = LabelEncoder()
    monthly["area_enc"] = area_encoder.fit_transform(monthly["area"])
    monthly["category_enc"] = category_encoder.fit_transform(monthly["category"])

    feature_cols = ["area_enc", "category_enc", "month", "year", "lag_1"]
    X = monthly[feature_cols]
    y = monthly["count"]

    # Time-based split: train on everything except the most recent 3 months per series,
    # test on those -- more honest than a random split for time series data.
    cutoff = monthly["month_index"].max() - 3
    train_mask = monthly["month_index"] <= cutoff
    X_train, X_test = X[train_mask], X[~train_mask]
    y_train, y_test = y[train_mask], y[~train_mask]

    model = RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42)
    model.fit(X_train, y_train)

    if len(X_test) > 0:
        y_pred = model.predict(X_test)
        mae = float(mean_absolute_error(y_test, y_pred))
        r2 = float(r2_score(y_test, y_pred))
    else:
        mae, r2 = None, None

    # Save the most recent known count per (area, category) -- used as the lag_1
    # feature when predicting a brand new future month.
    last_counts = (
        monthly.sort_values("month_index")
        .groupby(["area", "category"])
        .last()["count"]
        .to_dict()
    )

    joblib.dump(model, MODEL_PATH)
    joblib.dump({"area": area_encoder, "category": category_encoder}, ENCODERS_PATH)
    joblib.dump(last_counts, LAST_COUNTS_PATH)

    metrics = {
        "mae": round(mae, 2) if mae is not None else None,
        "r2": round(r2, 3) if r2 is not None else None,
        "trained_rows": len(monthly),
        "areas": sorted(area_encoder.classes_.tolist()),
        "categories": sorted(category_encoder.classes_.tolist()),
    }
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f)

    return metrics


if __name__ == "__main__":
    result = train()
    print("Training complete.")
    print(f"MAE: {result['mae']}  |  R2: {result['r2']}  |  Rows used: {result['trained_rows']}")
