"""The shared run envelope: run recording, stage recording, incident escalation.

The three entrypoints (fetch / enrich / classify) used to hand-roll this
bookkeeping and it drifted: enrich never escalated failures to incidents, and
failed stage runs were left "running" in two entrypoints while the third
marked them "failed". The envelope is where that behaviour lives once.

Tests cross the envelope's interface with an in-memory SQLite stand-in and a
recording issue client, asserting what lands in the DB — not internal state.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.db import init_db
from app.jobs import runner


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


class FakeIssueClient:
    def __init__(self) -> None:
        self.created: list[tuple] = []
        self.comments: list[tuple] = []
        self.closed: list[int] = []

    def create_issue(self, title: str, body: str, labels: list[str]):
        self.created.append((title, body, labels))
        return 1

    def add_comment(self, issue_number: int, body: str) -> None:
        self.comments.append((issue_number, body))

    def close_issue(self, issue_number: int) -> None:
        self.closed.append(issue_number)


def _run_id(conn) -> str:
    return conn.execute("SELECT run_id FROM pipeline_runs").fetchone()["run_id"]


def _incident(conn):
    return conn.execute("SELECT * FROM incidents").fetchone()


def test_success_completes_run_and_stages(conn) -> None:
    client = FakeIssueClient()
    code = runner.run_pipeline(
        run_type="fetch",
        conn=conn,
        issue_client=client,
        stages=[("fetch", lambda _ctx: {"inserted": 3})],
    )

    assert code == 0
    row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (_run_id(conn),)).fetchone()
    assert row["status"] == "success"
    assert row["run_type"] == "fetch"
    metrics = json.loads(row["metrics_json"])
    assert metrics["fetch"]["inserted"] == 3
    assert "duration_ms" in metrics["fetch"]
    assert len(metrics["fetch"]) == 2

    stage = conn.execute("SELECT * FROM stage_runs").fetchone()
    assert stage["stage_name"] == "fetch"
    assert stage["status"] == "success"
    assert client.created == []  # no incident escalation on success


def test_success_resolves_an_existing_incident(conn) -> None:
    client = FakeIssueClient()
    conn.execute(
        """
        INSERT INTO incidents (incident_key, kind, target_id, status, opened_at, updated_at, last_message)
        VALUES ('pipeline:fetch', 'pipeline-stage', 'fetch', 'open', 't', 't', 'still failing')
        """
    )
    conn.commit()

    code = runner.run_pipeline(
        run_type="fetch",
        conn=conn,
        issue_client=client,
        stages=[("fetch", lambda _ctx: {"inserted": 0})],
    )

    assert code == 0
    row = _incident(conn)
    assert row["status"] == "resolved"
    assert row["last_message"] == "Fetch pipeline completed successfully."


def test_failure_marks_run_and_stage_failed_and_escalates(conn) -> None:
    client = FakeIssueClient()

    def boom(_ctx) -> dict:
        raise RuntimeError("extraction chain exploded")

    code = runner.run_pipeline(
        run_type="classify",
        conn=conn,
        issue_client=client,
        stages=[("cluster", boom)],
    )

    assert code == 1
    run_id = _run_id(conn)
    row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row["status"] == "failed"
    assert "extraction chain exploded" in row["error_message"]

    stage = conn.execute("SELECT * FROM stage_runs").fetchone()
    assert stage["status"] == "failed"

    incident = _incident(conn)
    assert incident["incident_key"] == "pipeline:classify"
    assert incident["kind"] == "pipeline-stage"
    assert incident["target_id"] == "classify"
    assert incident["status"] == "open"
    # opened via the client, labelled sev2, carrying run id + traceback
    assert len(client.created) == 1
    _title, body, labels = client.created[0]
    assert "sev2" in labels
    assert run_id in body
    assert "Traceback" in body


def test_enrich_failures_now_escalate_too(conn) -> None:
    """Regression: the enrich entrypoint used to swallow failures silently."""
    client = FakeIssueClient()

    def boom(_ctx) -> dict:
        raise ValueError("quota state corrupt")

    code = runner.run_pipeline(
        run_type="enrich",
        conn=conn,
        issue_client=client,
        stages=[("enrich", boom)],
    )

    assert code == 1
    incident = _incident(conn)
    assert incident["incident_key"] == "pipeline:enrich"
    assert incident["status"] == "open"
    assert len(client.created) == 1


def test_multiple_stages_accumulate_metrics(conn) -> None:
    client = FakeIssueClient()
    code = runner.run_pipeline(
        run_type="classify",
        conn=conn,
        issue_client=client,
        stages=[
            ("cluster", lambda _ctx: {"events": 10}),
            ("categorize", lambda _ctx: {"events_categorized": 10}),
        ],
    )

    assert code == 0
    row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (_run_id(conn),)).fetchone()
    metrics = json.loads(row["metrics_json"])
    assert metrics["cluster"]["events"] == 10
    assert metrics["categorize"]["events_categorized"] == 10

    stages = conn.execute("SELECT stage_name, status FROM stage_runs ORDER BY stage_run_id").fetchall()
    assert [(s["stage_name"], s["status"]) for s in stages] == [
        ("cluster", "success"),
        ("categorize", "success"),
    ]


def test_failure_stops_later_stages(conn) -> None:
    client = FakeIssueClient()
    ran = []

    def boom(_ctx) -> dict:
        ran.append("export")
        raise RuntimeError("boom")

    code = runner.run_pipeline(
        run_type="fetch",
        conn=conn,
        issue_client=client,
        stages=[
            ("fetch", lambda _ctx: ran.append("fetch") or {"inserted": 1}),
            ("export", boom),
            ("extra", lambda _ctx: ran.append("extra") or {}),
        ],
    )

    assert code == 1
    assert ran == ["fetch", "export"]  # 'extra' never runs
    stages = conn.execute("SELECT stage_name, status FROM stage_runs ORDER BY stage_run_id").fetchall()
    assert [(s["stage_name"], s["status"]) for s in stages] == [
        ("fetch", "success"),
        ("export", "failed"),
    ]


def test_prepare_runs_before_run_creation_and_stages(conn) -> None:
    client = FakeIssueClient()
    order: list[str] = []

    def prepare(c):
        c.execute(
            """
            INSERT INTO sources (source_id, name, feed_url, default_category, enabled)
            VALUES ('prepped', 'Prepped', 'https://example.com/feed.xml', 'ai', 1)
            """
        )
        order.append("prepare")

    def stage_fn(_ctx) -> dict:
        order.append("stage")
        sources = conn.execute("SELECT source_id FROM sources").fetchall()
        assert [s["source_id"] for s in sources] == ["prepped"]
        return {"inserted": 1}

    code = runner.run_pipeline(
        run_type="fetch",
        conn=conn,
        issue_client=client,
        prepare=prepare,
        stages=[("fetch", stage_fn)],
    )

    assert code == 0
    assert order == ["prepare", "stage"]


def test_stage_receives_run_id_and_issue_client(conn) -> None:
    client = FakeIssueClient()
    seen = {}

    def stage_fn(ctx) -> dict:
        seen["run_id"] = ctx.run_id
        seen["client"] = ctx.issue_client
        seen["conn"] = ctx.conn
        return {"inserted": 1}

    code = runner.run_pipeline(
        run_type="enrich",
        conn=conn,
        issue_client=client,
        stages=[("enrich", stage_fn)],
    )

    assert code == 0
    assert seen["run_id"] == _run_id(conn)
    assert seen["client"] is client
    assert seen["conn"] is conn


def test_export_stage_writes_status_files(tmp_path, conn) -> None:
    client = FakeIssueClient()

    def export(ctx) -> dict:
        return runner.export_stage(
            ctx.conn,
            status_dir=tmp_path,
            summary=lambda c: {"open_incidents": 0},
            sources=lambda c: [],
            runs=lambda c: [],
            incidents=lambda c: [],
            events=lambda c: [],
            articles=lambda c: [],
        )

    code = runner.run_pipeline(
        run_type="fetch",
        conn=conn,
        issue_client=client,
        stages=[
            ("fetch", lambda _ctx: {"inserted": 0}),
            ("export", export),
        ],
    )

    assert code == 0
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "articles.json").exists()
    row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (_run_id(conn),)).fetchone()
    metrics = json.loads(row["metrics_json"])
    assert metrics["export"]["exported_files"] == 12