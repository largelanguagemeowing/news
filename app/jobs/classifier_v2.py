"""TF-IDF + cosine similarity kNN classifier.

Trains on manually labeled articles from data/status/articles.json
and saves a scikit-learn Pipeline to data/classifier/model.joblib.

The pipeline falls back to the keyword classifier (classifier.py) for
low-confidence predictions via the existing ml_classifier.py bridge.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline

MODEL_PATH = Path("data/classifier/model.joblib")


def build_training_data() -> list[dict]:
    articles_path = Path("data/status/articles.json")
    articles = json.loads(articles_path.read_text())
    labeled = []
    for a in articles:
        topic = a.get("topic")
        if topic and topic != "ai-models":
            labeled.append({
                "article_id": a["article_id"],
                "title": a["title"],
                "body": a.get("body", ""),
                "topic": topic,
            })
    return labeled


def prepare_text(title: str, body: str) -> str:
    body_clean = (body or "")[:8000]
    return f"{title} {title} {body_clean}"


def train() -> dict:
    labeled = build_training_data()
    if len(labeled) < 10:
        raise ValueError(f"Need at least 10 labeled articles, got {len(labeled)}")

    texts = [prepare_text(a["title"], a["body"]) for a in labeled]
    labels = [a["topic"] for a in labeled]

    unique_labels = sorted(set(labels))
    n_neighbors = min(7, len(unique_labels))

    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            max_features=8000,
            ngram_range=(1, 2),
            stop_words="english",
            sublinear_tf=True,
        )),
        ("knn", KNeighborsClassifier(
            n_neighbors=n_neighbors,
            metric="cosine",
            weights="distance",
        )),
    ])
    pipeline.fit(texts, labels)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": pipeline}, MODEL_PATH)

    result = {
        "trained": True,
        "samples": len(labeled),
        "classes": unique_labels,
        "model_path": str(MODEL_PATH),
    }
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    train()
