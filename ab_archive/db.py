from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from typing import Any, Iterable


@dataclass
class SearchResult:
    key: str
    title: str
    status: str
    source_type: str
    source_url: str
    text: str
    score: float


WINNER_STATUSES = ("Successful", "Implemented")

# Best available date for a test: end date, else start date, else Jira created.
_PERIOD_DATE = "COALESCE(NULLIF(end_date, ''), NULLIF(start_date, ''), NULLIF(created, ''))"
_PERIOD_SOURCE = (
    "CASE WHEN NULLIF(end_date, '') IS NOT NULL THEN 'end_date' "
    "WHEN NULLIF(start_date, '') IS NOT NULL THEN 'start_date' "
    "WHEN NULLIF(created, '') IS NOT NULL THEN 'created' ELSE NULL END"
)
_YEAR = f"CAST(substr({_PERIOD_DATE}, 1, 4) AS INTEGER)"
_QUARTER = f"((CAST(substr({_PERIOD_DATE}, 6, 2) AS INTEGER) + 2) / 3)"
_IS_WINNER = "(status IN ('Successful', 'Implemented'))"

# SQL select fragment adding derived fields to a tests row.
DERIVED_COLUMNS = (
    f"{_IS_WINNER} AS is_winner, {_YEAR} AS year, {_QUARTER} AS quarter, {_PERIOD_SOURCE} AS period_source"
)

GROUP_EXPRESSIONS = {
    "year": _YEAR,
    "quarter": f"printf('%d-Q%d', {_YEAR}, {_QUARTER})",
    "market": "COALESCE(NULLIF(market, ''), '(unknown)')",
    "page_type": "COALESCE(NULLIF(page_type, ''), '(unknown)')",
    "outcome": "CASE WHEN status IN ('Successful', 'Implemented') THEN 'Successful' ELSE status END",
}


class ArchiveDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tests (
                key TEXT PRIMARY KEY,
                issue_id TEXT,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                status_category TEXT,
                outcome TEXT,
                hypothesis TEXT,
                results TEXT,
                learnings TEXT,
                kpi TEXT,
                description TEXT,
                start_date TEXT,
                end_date TEXT,
                created TEXT,
                updated TEXT,
                labels_json TEXT NOT NULL DEFAULT '[]',
                jira_url TEXT NOT NULL,
                confluence_urls_json TEXT NOT NULL DEFAULT '[]',
                market TEXT,
                page_type TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT,
                UNIQUE(test_key, source_type, source_id),
                FOREIGN KEY(test_key) REFERENCES tests(key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                test_key TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding_json TEXT,
                UNIQUE(document_id, chunk_index),
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                text,
                test_key UNINDEXED,
                source_url UNINDEXED,
                content='chunks',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, text, test_key, source_url)
                SELECT new.id, new.text, new.test_key, d.source_url
                FROM documents d WHERE d.id = new.document_id;
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, text, test_key, source_url)
                VALUES('delete', old.id, old.text, old.test_key, '');
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, text, test_key, source_url)
                VALUES('delete', old.id, old.text, old.test_key, '');
                INSERT INTO chunks_fts(rowid, text, test_key, source_url)
                SELECT new.id, new.text, new.test_key, d.source_url
                FROM documents d WHERE d.id = new.document_id;
            END;

            CREATE TABLE IF NOT EXISTS sync_state (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE VIEW IF NOT EXISTS test_overview AS
            SELECT
                key,
                summary AS name,
                status,
                market,
                page_type,
                start_date,
                end_date,
                jira_url,
                labels_json AS labels,
                substr(hypothesis, 1, 600) AS hypothesis_preview,
                substr(results, 1, 600) AS results_preview,
                substr(learnings, 1, 600) AS learnings_preview
            FROM tests;

            CREATE VIEW IF NOT EXISTS document_overview AS
            SELECT
                d.test_key,
                t.summary AS test_name,
                t.status,
                d.source_type,
                d.title AS document_name,
                d.source_url,
                length(d.text) AS text_length,
                substr(d.text, 1, 1000) AS text_preview
            FROM documents d
            JOIN tests t ON t.key = d.test_key;

            CREATE VIEW IF NOT EXISTS chunk_overview AS
            SELECT
                c.test_key,
                t.summary AS test_name,
                t.status,
                d.source_type,
                d.title AS document_name,
                d.source_url,
                c.chunk_index,
                length(c.text) AS chunk_length,
                c.text AS chunk_text
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN tests t ON t.key = c.test_key;
            """
        )
        self.conn.commit()

    def upsert_test(self, record: dict[str, Any]) -> None:
        columns = [
            "key", "issue_id", "summary", "status", "status_category", "outcome",
            "hypothesis", "results", "learnings", "kpi", "description",
            "start_date", "end_date", "created", "updated", "labels_json",
            "jira_url", "confluence_urls_json", "market", "page_type", "raw_json",
            "synced_at",
        ]
        values = [record.get(column) for column in columns]
        update = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "key")
        placeholders = ", ".join("?" for _ in columns)
        self.conn.execute(
            f"""
            INSERT INTO tests ({", ".join(columns)}) VALUES ({placeholders})
            ON CONFLICT(key) DO UPDATE SET {update}
            """,
            values,
        )

    def test_updated_map(self) -> dict[str, str]:
        """Jira `updated` stamp per stored test, to tell which ones changed since last sync."""
        return {
            row["key"]: row["updated"] or ""
            for row in self.conn.execute("SELECT key, updated FROM tests").fetchall()
        }

    def delete_test(self, key: str) -> None:
        """Remove a test with its documents and chunks (cascades; triggers clean the FTS index)."""
        self.conn.execute("DELETE FROM tests WHERE key=?", (key,))

    def get_state(self, name: str) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE name=?", (name,)).fetchone()
        return row["value"] if row else None

    def set_state(self, name: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_state(name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (name, value),
        )

    def document_exists(self, test_key: str, source_type: str, source_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM documents WHERE test_key=? AND source_type=? AND source_id=? LIMIT 1",
            (test_key, source_type, source_id),
        ).fetchone()
        return row is not None

    def upsert_document(self, record: dict[str, Any]) -> int:
        self.conn.execute(
            """
            INSERT INTO documents (
                test_key, source_type, source_id, source_url, title, text, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(test_key, source_type, source_id) DO UPDATE SET
                source_url=excluded.source_url,
                title=excluded.title,
                text=excluded.text,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                record["test_key"], record["source_type"], record["source_id"],
                record["source_url"], record["title"], record["text"],
                record.get("raw_json", "{}"), record.get("updated_at"),
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM documents WHERE test_key=? AND source_type=? AND source_id=?",
            (record["test_key"], record["source_type"], record["source_id"]),
        ).fetchone()
        return int(row["id"])

    def replace_chunks(self, document_id: int, test_key: str, chunks: Iterable[str]) -> None:
        self.conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        self.conn.executemany(
            "INSERT INTO chunks(document_id, test_key, chunk_index, text) VALUES (?, ?, ?, ?)",
            [(document_id, test_key, index, text) for index, text in enumerate(chunks)],
        )

    def set_chunk_embedding(self, chunk_id: int, embedding: list[float]) -> None:
        self.conn.execute(
            "UPDATE chunks SET embedding_json=? WHERE id=?",
            (json.dumps(embedding), chunk_id),
        )

    def chunks_without_embeddings(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, text FROM chunks WHERE embedding_json IS NULL ORDER BY id"
        ).fetchall()

    def all_embedded_chunks(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT c.id, c.test_key, c.text, c.embedding_json, d.source_type, d.source_url,
                   t.summary, t.status
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN tests t ON t.key = c.test_key
            WHERE c.embedding_json IS NOT NULL
            """
        ).fetchall()

    def keyword_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        rows = self.conn.execute(
            """
            SELECT c.test_key, c.text, d.source_type, d.source_url, t.summary, t.status,
                   bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            JOIN tests t ON t.key = c.test_key
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [
            SearchResult(
                key=row["test_key"],
                title=row["summary"],
                status=row["status"],
                source_type=row["source_type"],
                source_url=row["source_url"],
                text=row["text"],
                score=float(-row["rank"]),
            )
            for row in rows
        ]

    def metadata_search(
        self,
        *,
        status: str | None = None,
        statuses: list[str] | None = None,
        page_type: str | None = None,
        market: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        clauses = []
        params: list[str | int] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"t.status IN ({placeholders})")
            params.extend(statuses)
        if status:
            clauses.append("t.status = ?")
            params.append(status)
        if page_type:
            clauses.append("t.page_type = ?")
            params.append(page_type)
        if market:
            clauses.append("t.market = ?")
            params.append(market)
        if not clauses:
            return []
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT t.key, t.summary, t.status, t.jira_url,
                   COALESCE(NULLIF(t.results, ''), NULLIF(t.hypothesis, ''), NULLIF(t.description, ''), t.summary) AS text
            FROM tests t
            WHERE {" AND ".join(clauses)}
            ORDER BY t.updated DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            SearchResult(
                key=row["key"],
                title=row["summary"],
                status=row["status"],
                source_type="jira",
                source_url=row["jira_url"],
                text=row["text"],
                score=100.0,
            )
            for row in rows
        ]

    def stats(self) -> dict[str, list[tuple[str, int]]]:
        result = {}
        for name, column in {
            "status": "status",
            "page_type": "page_type",
            "market": "market",
        }.items():
            rows = self.conn.execute(
                f"""
                SELECT COALESCE(NULLIF({column}, ''), '(unknown)') AS name, COUNT(*) AS count
                FROM tests GROUP BY 1 ORDER BY count DESC, name
                """
            ).fetchall()
            result[name] = [(row["name"], int(row["count"])) for row in rows]
        rows = self.conn.execute(
            """
            SELECT
                CASE
                    WHEN status IN ('Successful', 'Implemented') THEN 'Successful'
                    WHEN status IN ('Unsuccessful', 'No significant Result') THEN status
                    ELSE '(unknown)'
                END AS name,
                COUNT(*) AS count
            FROM tests
            GROUP BY 1
            ORDER BY count DESC, name
            """
        ).fetchall()
        result["outcome"] = [(row["name"], int(row["count"])) for row in rows]
        labels = self.conn.execute("SELECT labels_json FROM tests").fetchall()
        counts: dict[str, int] = {}
        for row in labels:
            for label in json.loads(row["labels_json"] or "[]"):
                counts[label] = counts.get(label, 0) + 1
        result["labels"] = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return result

    def aggregate(
        self,
        group_by: list[str],
        market: str | None = None,
        page_type: str | None = None,
        year: int | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        """Count tests per group with winner count and win rate (winners / total)."""
        for g in group_by:
            if g not in GROUP_EXPRESSIONS:
                raise ValueError(f"unknown group_by {g!r}; choose from {sorted(GROUP_EXPRESSIONS)}")
        clauses, params = [], []
        for column, value in (("market", market), ("page_type", page_type)):
            if value:
                clauses.append(f"{column} = ? COLLATE NOCASE")
                params.append(value)
        if year is not None:
            clauses.append(f"{_YEAR} = ?")
            params.append(year)
        if outcome:
            if outcome.lower() in ("successful", "implemented", "winner"):
                clauses.append(_IS_WINNER)
            else:
                clauses.append("status = ? COLLATE NOCASE")
                params.append(outcome)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        select = ", ".join(f"{GROUP_EXPRESSIONS[g]} AS {g}" for g in group_by)
        select = f"{select}, " if select else ""
        group = f"GROUP BY {', '.join(str(i + 1) for i in range(len(group_by)))} ORDER BY {', '.join(str(i + 1) for i in range(len(group_by)))}" if group_by else ""
        rows = self.conn.execute(
            f"""
            SELECT {select}COUNT(*) AS total, SUM({_IS_WINNER}) AS winners
            FROM tests {where} {group}
            """,
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["winners"] = int(d["winners"] or 0)
            d["win_rate"] = round(d["winners"] / d["total"], 3) if d["total"] else None
            out.append(d)
        return out

    def commit(self) -> None:
        self.conn.commit()
