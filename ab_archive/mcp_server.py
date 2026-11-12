from __future__ import annotations

import json
import sqlite3

from mcp.server.mcpserver import MCPServer

from .config import Config
from .metrics import parse_results
from .db import DERIVED_COLUMNS, GROUP_EXPRESSIONS, ArchiveDB
from .ollama import OllamaClient
from .retrieval import hybrid_search

mcp = MCPServer("ab-archive")

_config = Config.from_env()
_TEST_COLUMNS = (
    "key, summary, status, outcome, market, page_type, kpi, start_date, end_date, jira_url, "
    "(hypothesis <> '') AS has_hypothesis, idea, idea_reason, " + DERIVED_COLUMNS
)
_COMPACT_COLUMNS = "key, status, market, page_type, start_date, end_date, " + DERIVED_COLUMNS


def _open() -> ArchiveDB:
    db = ArchiveDB(_config.db_path)
    try:
        db.migrate()  # adds columns a newer version expects; a no-op once the sync has run
    except sqlite3.OperationalError:
        pass  # locked by a running sync, which migrates anyway
    db.conn.execute("PRAGMA query_only = ON")  # apart from that, the sync job is the only writer
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
    """Get one archived test by Jira key (e.g. AB-2655): hypothesis, results, learnings, KPI, and linked report text.
    When `hypothesis` is empty the ticket never had one. `idea` then says what was changed and where
    (taken literally from the ticket) and `idea_reason` quotes the ticket's own explanation of why, if it
    gave one. An empty `idea_reason` means the ticket does not say why: do not present a guess as fact."""
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
    test = {
        k: row[k]
        for k in row.keys()
        if k not in ("raw_json", "labels_json", "confluence_urls_json", "idea_stamp")
    }
    test["labels"] = json.loads(row["labels_json"] or "[]")
    test["metrics"] = parse_results(row["results"])
    test["documents"] = [dict(d) for d in docs]
    return test


@mcp.tool()
def list_tests(
    status: str | None = None,
    market: str | None = None,
    page_type: str | None = None,
    year: int | None = None,
    winners_only: bool = False,
    limit: int = 25,
    offset: int = 0,
    compact: bool = False,
) -> list[dict]:
    """List archived tests, newest first. Filters: status (Successful, Implemented, Unsuccessful,
    No significant Result), market (e.g. HSNL), page_type (e.g. checkout, property, funnel), year
    (from end date, else start date, else created) and winners_only. "Successful" and "Implemented"
    are treated as the same (both winners) and match either status. Every row has is_winner, year,
    quarter and period_source (which date the year/quarter came from). Page with offset; compact=true
    returns only key, status, market, page_type, dates and the derived fields. For tests without a
    written hypothesis (has_hypothesis = 0), `idea` gives the change and its scope in the ticket's own
    words and `idea_reason` quotes the ticket's stated reason when there is one."""
    clauses, params = [], []
    if winners_only or (status and status.lower() in ("successful", "implemented")):
        clauses.append("status IN ('Successful', 'Implemented')")
        status = None
    for column, value in (("status", status), ("market", market), ("page_type", page_type)):
        if value:
            clauses.append(f"{column} = ? COLLATE NOCASE")
            params.append(value)
    if year is not None:
        clauses.append(f"{GROUP_EXPRESSIONS['year']} = ?")
        params.append(year)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    columns = _COMPACT_COLUMNS if compact else _TEST_COLUMNS
    db = _open()
    try:
        rows = db.conn.execute(
            f"SELECT {columns} FROM tests {where} ORDER BY updated DESC LIMIT ? OFFSET ?",
            (*params, limit, max(offset, 0)),
        ).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = {**dict(r), "is_winner": bool(r["is_winner"])}
        if "has_hypothesis" in d:
            d["has_hypothesis"] = bool(d["has_hypothesis"])
        out.append(d)
    return out


@mcp.tool()
def aggregate_tests(
    group_by: list[str] | None = None,
    market: str | None = None,
    page_type: str | None = None,
    year: int | None = None,
    outcome: str | None = None,
) -> list[dict]:
    """Count tests grouped by any of: year, quarter, market, page_type, outcome. Each group returns
    total, winners (Successful + Implemented) and win_rate (winners/total). Optional filters: market,
    page_type, year, outcome ("winner"/"Successful"/"Implemented" = winners, or Unsuccessful /
    No significant Result). Year/quarter come from end date, else start date, else created.
    Example: group_by=["year"] gives winning tests per year; group_by=["market","page_type"] a matrix."""
    db = _open()
    try:
        return db.aggregate(group_by or [], market=market, page_type=page_type, year=year, outcome=outcome)
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        db.close()


_RANK_METRICS = ("cr_change_pct", "rpo_change_pct", "revenue_change_pct", "confidence_pct")


@mcp.tool()
def rank_tests(
    metric: str = "cr_change_pct",
    order: str = "desc",
    market: str | None = None,
    page_type: str | None = None,
    year: int | None = None,
    winners_only: bool = False,
    min_confidence: float | None = None,
    limit: int = 20,
) -> dict:
    """Rank tests by a result number parsed from the report text: cr_change_pct, rpo_change_pct,
    revenue_change_pct (relative % change, variation vs control) or confidence_pct. order is "desc"
    (biggest uplift first) or "asc" (biggest drop first). Numbers exist only for tests whose results
    text states them (mostly 2024+: roughly 12% have a CR change, ~5% revenue, ~1% RPO), so "coverage"
    in the response says how many tests were rankable. confidence_pct is the figure quoted in the
    report; confidence_favors says whether it was for the variation or the control. Each row also
    carries control_cr_pct / variation_cr_pct when stated."""
    if metric not in _RANK_METRICS:
        return {"error": f"unknown metric {metric!r}; choose from {list(_RANK_METRICS)}"}
    clauses, params = ["results IS NOT NULL AND results <> ''"], []
    if winners_only:
        clauses.append("status IN ('Successful', 'Implemented')")
    for column, value in (("market", market), ("page_type", page_type)):
        if value:
            clauses.append(f"{column} = ? COLLATE NOCASE")
            params.append(value)
    if year is not None:
        clauses.append(f"{GROUP_EXPRESSIONS['year']} = ?")
        params.append(year)
    db = _open()
    try:
        rows = db.conn.execute(
            f"SELECT key, summary, status, market, page_type, results, {DERIVED_COLUMNS} FROM tests WHERE {' AND '.join(clauses)}",
            params,
        ).fetchall()
    finally:
        db.close()
    ranked = []
    for r in rows:
        m = parse_results(r["results"])
        if m[metric] is None:
            continue
        if min_confidence is not None and (m["confidence_pct"] is None or m["confidence_pct"] < min_confidence):
            continue
        ranked.append({
            "key": r["key"], "summary": r["summary"], "status": r["status"], "market": r["market"],
            "page_type": r["page_type"], "year": r["year"], "is_winner": bool(r["is_winner"]),
            **{k: v for k, v in m.items() if v is not None},
        })
    ranked.sort(key=lambda t: t[metric], reverse=order.lower() != "asc")
    return {"metric": metric, "coverage": len(ranked), "tests_with_results_text": len(rows), "tests": ranked[:limit]}


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
