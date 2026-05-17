from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_MODEL_PATH = Path("data/classifier/model.joblib")


def prepare_text(title: str, body: str) -> str:
    body_clean = (body or "")[:8000]
    return f"{title} {title} {body_clean}"


@lru_cache(maxsize=1)
def load_classifier(model_path: str = str(DEFAULT_MODEL_PATH)) -> dict[str, Any] | None:
    path = Path(model_path)
    if not path.exists():
        return None
    import joblib

    return joblib.load(path)


def classify_with_model(
    title: str,
    body: str,
    *,
    default_category: str = "general",
    model_path: str = str(DEFAULT_MODEL_PATH),
) -> tuple[str, float] | None:
    payload = load_classifier(model_path)
    if not payload:
        return None
    model = payload["model"]
    text = prepare_text(title, body)
    label = str(model.predict([text])[0])
    confidence = 0.7
    if hasattr(model, "predict_proba"):
        classes = list(model.classes_)
        probabilities = model.predict_proba([text])[0]
        confidence = float(probabilities[classes.index(label)])
    if confidence < 0.45:
        return None
    return label, round(confidence, 2)
