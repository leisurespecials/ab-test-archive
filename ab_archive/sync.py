from __future__ import annotations

from datetime import datetime, timezone
import json
from collections.abc import Callable
from typing import Any

from .atlassian import AtlassianClient, issue_web_url, json_dumps, page_web_url
from .config import Config
from .db import ArchiveDB
from .retrieval import chunk_text, embed_missing
from .ollama import OllamaClient
from .text import (
    adf_to_text,
    compact_text,
    extract_confluence_page_ids,
    infer_market,
    infer_page_type,
)


def sync_archive(
    config: Config,
    *,
    embed: bool = True,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, int, int]:
    def log(message: str) -> None:
        if progress:
            progress(message)

    config.require_jira_auth()
    log(f"Opening local archive database: {config.db_path}")
    db = ArchiveDB(config.db_path)
    db.migrate()
    client = AtlassianClient(config.jira_base_url, config.jira_email, config.jira_api_token)
    synced_issues = 0
    synced_reports = 0

    log("Starting Jira sync for project AB / work type AB Test / final statuses only.")
    for issue in client.iter_ab_test_issues(progress=log):
        record, jira_doc_text = normalize_issue(config.jira_base_url, issue)
        log(f"Processing {record['key']} [{record['status']}]: {record['summary']}")
        db.upsert_test(record)
        doc_id = db.upsert_document(
            {
                "test_key": record["key"],
                "source_type": "jira",
                "source_id": record["key"],
                "source_url": record["jira_url"],
                "title": record["summary"],
                "text": jira_doc_text,
                "raw_json": record["raw_json"],
                "updated_at": record["updated"],
            }
        )
        db.replace_chunks(doc_id, record["key"], chunk_text(jira_doc_text))

        confluence_page_ids = json.loads(record["confluence_urls_json"])
        if confluence_page_ids:
            log(f"  Found {len(confluence_page_ids)} linked Confluence report(s).")
        for page_id in confluence_page_ids:
            log(f"  Fetching Confluence page {page_id}...")
            try:
                page = client.get_confluence_page(page_id)
            except RuntimeError as exc:
                log(f"  Could not fetch Confluence page {page_id} for {record['key']}: {exc}")
                continue
            page_text = page.get("_archive_text", "")
            page_url = page_web_url(config.jira_base_url, page_id)
            report_id = db.upsert_document(
                {
                    "test_key": record["key"],
                    "source_type": "confluence",
                    "source_id": page_id,
                    "source_url": page_url,
                    "title": page.get("title") or f"Confluence page {page_id}",
                    "text": page_text,
                    "raw_json": json_dumps(page),
                    "updated_at": ((page.get("version") or {}).get("createdAt")),
                }
            )
            db.replace_chunks(report_id, record["key"], chunk_text(page_text))
            synced_reports += 1

        synced_issues += 1
        if synced_issues % 25 == 0:
            db.commit()
            log(f"Saved progress: {synced_issues} Jira work items, {synced_reports} Confluence reports.")

    embedded = 0
    if embed:
        try:
            ollama = OllamaClient(config.ollama_base_url, config.ollama_chat_model, config.ollama_embed_model)
            embedded = embed_missing(db, ollama, progress=log)
        except RuntimeError as exc:
            log(f"Skipped embeddings: {exc}")
    db.commit()
    db.close()
    log(f"Finished sync: {synced_issues} Jira work items, {synced_reports} Confluence reports, {embedded} embeddings.")
    return synced_issues, synced_reports, embedded


def normalize_issue(base_url: str, issue: dict[str, Any]) -> tuple[dict[str, Any], str]:
    fields = issue.get("fields") or {}
    key = issue["key"]
    summary = fields.get("summary") or ""
    status = (fields.get("status") or {}).get("name", "")
    status_category = ((fields.get("status") or {}).get("statusCategory") or {}).get("name", "")
    description = adf_to_text(fields.get("description"))
    hypothesis = adf_to_text(fields.get("customfield_10601"))
    learnings = adf_to_text(fields.get("customfield_10700"))
    results = adf_to_text(fields.get("customfield_11901"))
    kpi = adf_to_text(fields.get("customfield_12602"))
    outcome = adf_to_text(fields.get("customfield_12603")) or status
    confluence_page_ids = extract_confluence_page_ids("\n".join([description, hypothesis, learnings, results, kpi]))
    jira_url = issue.get("webUrl") or issue_web_url(base_url, key)
    now = datetime.now(timezone.utc).isoformat()
    page_type = infer_page_type(summary, description)

    jira_doc_text = compact_text(
        f"{key}: {summary}",
        f"Status: {status}",
        f"Market: {infer_market(summary)}" if infer_market(summary) else "",
        f"Page type: {page_type}" if page_type else "",
        f"Hypothesis:\n{hypothesis}" if hypothesis else "",
        f"KPI:\n{kpi}" if kpi else "",
        f"Description:\n{description}" if description else "",
        f"Results:\n{results}" if results else "",
        f"Learnings:\n{learnings}" if learnings else "",
    )

    record = {
        "key": key,
        "issue_id": issue.get("id"),
        "summary": summary,
        "status": status,
        "status_category": status_category,
        "outcome": outcome,
        "hypothesis": hypothesis,
        "results": results,
        "learnings": learnings,
        "kpi": kpi,
        "description": description,
        "start_date": fields.get("customfield_10602"),
        "end_date": fields.get("customfield_10603"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "labels_json": json_dumps(fields.get("labels") or []),
        "jira_url": jira_url,
        "confluence_urls_json": json_dumps(confluence_page_ids),
        "market": infer_market(summary),
        "page_type": page_type,
        "raw_json": json_dumps(issue),
        "synced_at": now,
    }
    return record, jira_doc_text
