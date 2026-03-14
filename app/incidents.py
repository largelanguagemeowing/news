from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

import requests

from app.utils import utc_now_iso


@dataclass
class IncidentSignal:
    key: str
    kind: str
    target_id: str
    message: str
    severity: str = "sev3"


class GitHubIssueClient:
    def __init__(self) -> None:
        self.token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY", "")
        self.repo_owner = ""
        self.repo_name = ""
        if "/" in repo:
            self.repo_owner, self.repo_name = repo.split("/", 1)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.repo_owner and self.repo_name)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def create_issue(self, title: str, body: str, labels: list[str]) -> Optional[int]:
        if not self.enabled:
            return None
        url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues"
        payload = {"title": title, "body": body, "labels": labels}
        response = requests.post(url, headers=self._headers(), json=payload, timeout=20)
        if response.status_code >= 300:
            return None
        return int(response.json()["number"])

    def add_comment(self, issue_number: int, body: str) -> None:
        if not self.enabled:
            return
        url = (
            f"https://api.github.com/repos/{self.repo_owner}/"
            f"{self.repo_name}/issues/{issue_number}/comments"
        )
        requests.post(url, headers=self._headers(), json={"body": body}, timeout=20)

    def close_issue(self, issue_number: int) -> None:
        if not self.enabled:
            return
        url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues/{issue_number}"
        requests.patch(url, headers=self._headers(), json={"state": "closed"}, timeout=20)


def sync_incident_open_or_update(
    conn: sqlite3.Connection,
    signal: IncidentSignal,
    run_id: str,
    client: GitHubIssueClient,
) -> None:
    now = utc_now_iso()
    row = conn.execute(
        "SELECT incident_id, status, issue_number FROM incidents WHERE incident_key = ?",
        (signal.key,),
    ).fetchone()
    if row is None:
        title = f"[incident] {signal.key}"
        body = (
            f"Incident key: `{signal.key}`\n\n"
            f"Kind: `{signal.kind}`\n\n"
            f"Target: `{signal.target_id}`\n\n"
            f"First seen in run: `{run_id}`\n\n"
            f"Details:\n{signal.message}\n"
        )
        labels = ["incident", signal.kind, signal.severity]
        issue_number = client.create_issue(title=title, body=body, labels=labels)
        conn.execute(
            """
            INSERT INTO incidents
              (incident_key, kind, target_id, status, opened_at, updated_at, issue_number, last_message)
            VALUES (?, ?, ?, 'open', ?, ?, ?, ?)
            """,
            (signal.key, signal.kind, signal.target_id, now, now, issue_number, signal.message),
        )
        return

    issue_number = row["issue_number"]
    conn.execute(
        """
        UPDATE incidents
        SET status = 'open',
            updated_at = ?,
            resolved_at = NULL,
            last_message = ?
        WHERE incident_key = ?
        """,
        (now, signal.message, signal.key),
    )
    if issue_number:
        client.add_comment(
            int(issue_number),
            f"Run `{run_id}` still failing.\n\n{signal.message}",
        )


def sync_incident_resolve(
    conn: sqlite3.Connection,
    incident_key: str,
    run_id: str,
    resolution_message: str,
    client: GitHubIssueClient,
) -> None:
    row = conn.execute(
        "SELECT incident_id, status, issue_number FROM incidents WHERE incident_key = ?",
        (incident_key,),
    ).fetchone()
    if row is None or row["status"] == "resolved":
        return
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE incidents
        SET status = 'resolved',
            updated_at = ?,
            resolved_at = ?,
            last_message = ?
        WHERE incident_key = ?
        """,
        (now, now, resolution_message, incident_key),
    )
    issue_number = row["issue_number"]
    if issue_number:
        client.add_comment(int(issue_number), f"Resolved in run `{run_id}`.\n\n{resolution_message}")
        client.close_issue(int(issue_number))

