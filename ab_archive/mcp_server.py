from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from .config import Config
from .db import ArchiveDB
from .ollama import OllamaClient
from .retrieval import hybrid_search

mcp = MCPServer("ab-archive")

_config = Config.from_env()
_TEST_COLUMNS = "key, summary, status, outcome, market, page_type, kpi, start_date, end_date, jira_url"


def _open() -> ArchiveDB:
    db = ArchiveDB(_config.db_path)
    db.conn.execute("PRAGMA query_only = ON")  # the sync job is the only writer
    return db


@mcp.tool()
def search_tests(query: str, limit: int = 8) -> list[dict]:
    """Search archived A/B tests (hybrid keyword + semantic). Returns matching text snippets with Jira keys."""
    db = _open()
    try:
        ollama = OllamaClient(_config.ollama_base_url, _config.ollama_chat_model, _config.ollama_embed_model)
        results = hybrid_search(db, query, ollama, limit=limit)
    finally:
        db.close()
    return [
        {
            "key": r.key,
            "title": r.title,
            "status": r.status,
            "source_type": r.source_type,
            "url": r.source_url,
            "text": r.text,
        }
        for r in results
    ]


@mcp.tool()
def get_test(key: str) -> dict:
    """Get one archived test by Jira key (e.g. AB-2655): hypothesis, results, learnings, KPI, and linked report text."""
    db = _open()
    try:
        row = db.conn.execute("SELECT * FROM tests WHERE key = ?", (key.upper(),)).fetchone()
        if row is None:
            return {"error": f"{key} not found in archive"}
        docs = db.conn.execute(
            "SELECT source_type, source_url, title, text FROM documents WHERE test_key = ? ORDER BY source_type, source_id",
            (row["key"],),
        ).fetchall()
    finally:
        db.close()
    test = {k: row[k] for k in row.keys() if k not in ("raw_json", "labels_json", "confluence_urls_json")}
    test["labels"] = json.loads(row["labels_json"] or "[]")
    test["documents"] = [dict(d) for d in docs]
    return test


@mcp.tool()
def list_tests(
    status: str | None = None,
    market: str | None = None,
    page_type: str | None = None,
    limit: int = 25,
) -> list[dict]:
    """List archived tests, newest first, filtered by status (Successful, Implemented, Unsuccessful,
    No significant Result), market (e.g. HSNL) and/or page_type (e.g. checkout, property, funnel)."""
    clauses, params = [], []
    for column, value in (("status", status), ("market", market), ("page_type", page_type)):
        if value:
            clauses.append(f"{column} = ? COLLATE NOCASE")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    db = _open()
    try:
        rows = db.conn.execute(
            f"SELECT {_TEST_COLUMNS} FROM tests {where} ORDER BY updated DESC LIMIT ?", (*params, limit)
        ).fetchall()
    finally:
        db.close()
    return [dict(r) for r in rows]


@mcp.tool()
def archive_stats() -> dict:
    """Counts of archived tests by status, page type, market and label."""
    db = _open()
    try:
        return {group: dict(rows) for group, rows in db.stats().items()}
    finally:
        db.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
