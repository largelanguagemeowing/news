from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline


MODEL_VERSION = "tfidf-logreg-v1"
TRAINING_QUALITIES = {"high", "medium"}


def _load_training_rows(db_path: str, labels_path: str, min_class_count: int) -> list[dict[str, Any]]:
    raw_labels = [
        item
        for item in json.loads(Path(labels_path).read_text(encoding="utf-8"))
        if item.get("quality") in TRAINING_QUALITIES
    ]
    label_counts: dict[str, int] = {}
    for item in raw_labels:
        label_counts[item["label"]] = label_counts.get(item["label"], 0) + 1
    trainable_labels = {label for label, count in label_counts.items() if count >= min_class_count}
    labels = {
        item["article_id"]: item
        for item in raw_labels
        if item["label"] in trainable_labels
    }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT article_id, title, body, source_id, extraction_method
        FROM articles
        ORDER BY published_at DESC
        """
    ).fetchall()
    training_rows: list[dict[str, Any]] = []
    for row in rows:
        label = labels.get(row["article_id"])
        if not label:
            continue
        body = str(row["body"] or "")
        if len(body) < 120:
            continue
        training_rows.append(
            {
                "article_id": row["article_id"],
                "text": f"{row['title']}\n\n{body[:8000]}",
                "label": label["label"],
                "quality": label["quality"],
                "source_id": row["source_id"],
                "extraction_method": row["extraction_method"],
            }
        )
    return training_rows


def train_classifier(
    *,
    db_path: str,
    labels_path: str,
    model_path: str,
    metrics_path: str,
    min_class_count: int = 30,
) -> dict[str, Any]:
    rows = _load_training_rows(db_path, labels_path, min_class_count)
    if len(rows) < 100:
        raise RuntimeError(f"not enough training rows: {len(rows)}")

    texts = [row["text"] for row in rows]
    labels = [row["label"] for row in rows]
    label_counts = {label: labels.count(label) for label in sorted(set(labels))}
    stratify = labels if min(label_counts.values()) >= 2 else None

    x_train, x_test, y_train, y_test = train_test_split(
        texts,
        labels,
        test_size=0.2,
        random_state=42,
        stratify=stratify,
    )
    model = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=2,
                    max_df=0.9,
                    max_features=12000,
                    sublinear_tf=True,
                    strip_accents="unicode",
                ),
            ),
            (
                "classifier",
                LogisticRegression(
                    C=3.0,
                    class_weight="balanced",
                    max_iter=1000,
                    random_state=42,
                ),
            ),
        ]
    )
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    report = classification_report(y_test, predictions, output_dict=True, zero_division=0)
    metrics = {
        "model_version": MODEL_VERSION,
        "training_rows": len(rows),
        "test_rows": len(y_test),
        "accuracy": round(float(accuracy_score(y_test, predictions)), 4),
        "labels": label_counts,
        "min_class_count": min_class_count,
        "classification_report": report,
    }

    model_output = Path(model_path)
    model_output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"version": MODEL_VERSION, "model": model, "labels": sorted(set(labels))}, model_output)
    Path(metrics_path).write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a TF-IDF classifier from weak labels and enriched bodies.")
    parser.add_argument("--db", default="data/news.db")
    parser.add_argument("--labels", default="data/classifier/weak_labels.json")
    parser.add_argument("--model", default="data/classifier/model.joblib")
    parser.add_argument("--metrics", default="data/classifier/training_metrics.json")
    parser.add_argument("--min-class-count", type=int, default=30)
    args = parser.parse_args()
    metrics = train_classifier(
        db_path=args.db,
        labels_path=args.labels,
        model_path=args.model,
        metrics_path=args.metrics,
        min_class_count=args.min_class_count,
    )
    print(json.dumps({k: metrics[k] for k in ("model_version", "training_rows", "test_rows", "accuracy")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
