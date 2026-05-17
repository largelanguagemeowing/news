import json
import sqlite3

from app.jobs.train_classifier import train_classifier


def test_train_classifier_writes_model_and_metrics(tmp_path) -> None:
    db_path = tmp_path / "news.db"
    labels_path = tmp_path / "labels.json"
    model_path = tmp_path / "model.joblib"
    metrics_path = tmp_path / "metrics.json"

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE articles (
          article_id INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          source_id TEXT NOT NULL,
          extraction_method TEXT NOT NULL,
          published_at TEXT NOT NULL
        );
        """
    )
    labels = []
    article_id = 1
    examples = {
        "security": "vulnerability exploit cve malware breach security " * 20,
        "research": "research paper benchmark dataset training architecture " * 20,
        "agents": "agent agents agentic codex tool calling workflow " * 20,
    }
    for label, body in examples.items():
        for idx in range(50):
            conn.execute(
                "INSERT INTO articles VALUES (?, ?, ?, 'test', 'trafilatura', '2026-01-01T00:00:00+00:00')",
                (article_id, f"{label} example {idx}", body),
            )
            labels.append({"article_id": article_id, "label": label, "quality": "high"})
            article_id += 1
    conn.commit()
    labels_path.write_text(json.dumps(labels), encoding="utf-8")

    metrics = train_classifier(
        db_path=str(db_path),
        labels_path=str(labels_path),
        model_path=str(model_path),
        metrics_path=str(metrics_path),
        min_class_count=2,
    )

    assert model_path.exists()
    assert metrics_path.exists()
    assert metrics["training_rows"] == 150
