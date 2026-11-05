from __future__ import annotations

import json
from pathlib import Path

from .db import ArchiveDB

TEST_FIELDS = [
    "key", "summary", "status", "outcome", "hypothesis", "results", "learnings", "kpi",
    "description", "start_date", "end_date", "created", "updated", "jira_url",
    "market", "page_type",
]


def export_archive(db: ArchiveDB, out_dir: Path) -> tuple[int, int, int]:
    """Write one deterministic JSON file per test (no embeddings, no raw Jira payloads).

    Output is stable across runs so unchanged tests produce no git diff.
    Returns (tests, files_changed, files_removed).
    """
    tests_dir = out_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    rows = db.conn.execute("SELECT * FROM tests ORDER BY key").fetchall()

    changed = 0
    keep: set[str] = set()
    for row in rows:
        docs = db.conn.execute(
            "SELECT source_type, source_id, source_url, title, text, updated_at "
            "FROM documents WHERE test_key = ? ORDER BY source_type, source_id",
            (row["key"],),
        ).fetchall()
        payload = {field: row[field] for field in TEST_FIELDS}
        payload["labels"] = json.loads(row["labels_json"] or "[]")
        payload["confluence_urls"] = json.loads(row["confluence_urls_json"] or "[]")
        payload["documents"] = [dict(doc) for doc in docs]
        name = f"{row['key']}.json"
        keep.add(name)
        if _write_if_changed(tests_dir / name, payload):
            changed += 1

    removed = 0
    for path in tests_dir.glob("*.json"):
        if path.name not in keep:
            path.unlink()
            removed += 1

    # Only things that change when the data does, so a quiet day means no commit.
    manifest = {
        "test_count": len(rows),
        "latest_updated": max((row["updated"] or "" for row in rows), default=""),
    }
    if _write_if_changed(out_dir / "manifest.json", manifest):
        changed += 1
    return len(rows), changed, removed


def _write_if_changed(path: Path, payload: dict) -> bool:
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True
