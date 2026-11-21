"""
Loads the trained model and predicts next month's crime count for a given
(area, category) pair. Used by app.py's /prediction/run route.
"""
import os

import joblib
import pandas as pd

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.pkl")
ENCODERS_PATH = os.path.join(os.path.dirname(__file__), "encoders.pkl")
LAST_COUNTS_PATH = os.path.join(os.path.dirname(__file__), "last_counts.pkl")


def model_exists() -> bool:
    return os.path.exists(MODEL_PATH) and os.path.exists(ENCODERS_PATH)


def predict(area: str, category: str, target_year: int, target_month: int) -> dict:
    """Returns {'predicted_count': int, 'error': str|None}."""
    if not model_exists():
        return {"predicted_count": None, "error": "Model has not been trained yet."}

    model = joblib.load(MODEL_PATH)
    encoders = joblib.load(ENCODERS_PATH)
    last_counts = joblib.load(LAST_COUNTS_PATH)

    area_encoder = encoders["area"]
    category_encoder = encoders["category"]

    if area not in area_encoder.classes_:
        return {"predicted_count": None, "error": f"'{area}' was not seen during training."}
    if category not in category_encoder.classes_:
        return {"predicted_count": None, "error": f"'{category}' was not seen during training."}

    area_enc = area_encoder.transform([area])[0]
    category_enc = category_encoder.transform([category])[0]

    # Use the most recent known monthly count for this (area, category) as the
    # lag feature -- reasonable proxy for "recent trend" when predicting ahead.
    lag_1 = last_counts.get((area, category), 0)

    features = pd.DataFrame(
        [[area_enc, category_enc, target_month, target_year, lag_1]],
        columns=["area_enc", "category_enc", "month", "year", "lag_1"],
    )
    prediction = model.predict(features)[0]
    predicted_count = max(0, round(prediction))

    return {"predicted_count": predicted_count, "error": None}
