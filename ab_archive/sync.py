from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
    pdf_to_text,
    select_pdf_attachments,
)


# Incremental runs only re-fetch work items whose Jira `updated` stamp changed. An edit to a
# linked Confluence report does not touch that stamp, so everything is re-fetched once in a
# while to pick those up (and to retry PDFs/reports that could not be read before).
FULL_SYNC_INTERVAL = timedelta(days=7)

# Leaving scope is normal (a test gets dismissed or reopened) but a listing that suddenly
# misses a large share of the archive points at a Jira or permissions problem instead, so
# removal is skipped rather than wiping local data on a bad response.
MAX_PRUNE_FRACTION = 0.2
MIN_PRUNE_ALLOWANCE = 25


@dataclass
class _Run:
    pdf_limit: int = 0
    issues: int = 0
    reports: int = 0
    pdfs: int = 0
    pdf_limit_reached: bool = False
    failed: list[str] = field(default_factory=list)


def sync_archive(
    config: Config,
    *,
    embed: bool = True,
    pdf_limit: int = 0,
    full: bool = False,
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
    run = _Run(pdf_limit=pdf_limit)
    started = datetime.now(timezone.utc)

    def finish_embeddings() -> int:
        if not embed:
            return 0
        try:
            ollama = OllamaClient(config.ollama_base_url, config.ollama_chat_model, config.ollama_embed_model)
            return embed_missing(db, ollama, progress=log)
        except RuntimeError as exc:
            log(f"Skipped embeddings: {exc}")
            return 0

    log("Listing Jira work items in scope (project AB / work type AB Test / final statuses only).")
    try:
        index = dict(client.iter_scope_index(progress=log))
    except RuntimeError:
        # Jira is unreachable, but chunks left unembedded by an earlier run can still be finished.
        finish_embeddings()
        db.commit()
        db.close()
        raise

    local = db.test_updated_map()
    full = full or _full_sync_due(db.get_state("last_full_sync"), started)
    if full:
        keys = list(index)
        log(f"Full sync: fetching all {len(keys)} work items.")
    else:
        keys = [key for key, updated in index.items() if local.get(key) != updated]
        log(f"Incremental sync: {len(keys)} of {len(index)} work items are new or changed.")

    for start in range(0, len(keys), 100):
        batch = keys[start : start + 100]
        seen: set[str] = set()
        try:
            for issue in client.iter_ab_test_issues(progress=log, keys=batch):
                seen.add(issue["key"])
                _sync_issue_safely(db, client, config, issue, run, log)
                if run.issues and run.issues % 25 == 0:
                    db.commit()
                    log(f"Saved progress: {run.issues} Jira work items, {run.reports} reports.")
        except RuntimeError as exc:
            # The search request itself failed after retries; the items of this batch that were
            # not reached keep their old `updated` stamp and are picked up again next run.
            missed = [key for key in batch if key not in seen]
            run.failed.extend(missed)
            log(f"WARNING: could not fetch {len(missed)} work item(s), will retry next run: {exc}")

    removed = _prune_out_of_scope(db, local, index, log)
    if full:
        db.set_state("last_full_sync", started.isoformat())
    db.set_state("last_sync", started.isoformat())
    db.commit()

    embedded = finish_embeddings()
    db.commit()
    db.close()
    log(
        f"Finished sync: {run.issues} Jira work items, {run.reports} Confluence reports, "
        f"{run.pdfs} PDF attachments extracted, {removed} removed, {embedded} embeddings."
    )
    if run.failed:
        log(f"WARNING: {len(run.failed)} work item(s) failed and will be retried next run: {', '.join(run.failed)}")
    return run.issues, run.reports, embedded


def _full_sync_due(last_full: str | None, now: datetime) -> bool:
    if not last_full:
        return True
    try:
        return now - datetime.fromisoformat(last_full) >= FULL_SYNC_INTERVAL
    except ValueError:
        return True


def _prune_out_of_scope(
    db: ArchiveDB,
    local: dict[str, str],
    index: dict[str, str],
    log: Callable[[str], None],
) -> int:
    """Delete archived tests that Jira no longer lists in scope (e.g. moved to Dismissed)."""
    stale = sorted(key for key in local if key not in index)
    if not stale:
        return 0
    allowance = max(MIN_PRUNE_ALLOWANCE, int(len(local) * MAX_PRUNE_FRACTION))
    if not index or len(stale) > allowance:
        log(
            f"WARNING: {len(stale)} archived work items are missing from the Jira scope listing "
            f"(limit {allowance}); not removing anything. Check Jira access and the scope filter."
        )
        return 0
    for key in stale:
        db.delete_test(key)
        log(f"Removed {key}: no longer in scope (status changed or deleted in Jira).")
    return len(stale)


def _sync_issue_safely(
    db: ArchiveDB,
    client: AtlassianClient,
    config: Config,
    issue: dict[str, Any],
    run: _Run,
    log: Callable[[str], None],
) -> None:
    """Sync one work item atomically: on any failure nothing of it is stored, so its old
    `updated` stamp stays and the next run fetches it again. One bad item never stops the run."""
    key = issue.get("key", "?")
    before = (run.reports, run.pdfs, run.pdf_limit_reached)
    db.conn.execute("SAVEPOINT sync_issue")
    try:
        _sync_issue(db, client, config, issue, run, log)
    except Exception as exc:
        db.conn.execute("ROLLBACK TO sync_issue")
        db.conn.execute("RELEASE sync_issue")
        run.reports, run.pdfs, run.pdf_limit_reached = before
        run.failed.append(key)
        log(f"WARNING: skipped {key}, will retry next run: {exc}")
        return
    db.conn.execute("RELEASE sync_issue")
    run.issues += 1


def _sync_issue(
    db: ArchiveDB,
    client: AtlassianClient,
    config: Config,
    issue: dict[str, Any],
    run: _Run,
    log: Callable[[str], None],
) -> None:
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
        run.reports += 1

    comments = client.get_issue_comments(record["key"])
    comment_parts = []
    if comments:
        for c in comments:
            author = ((c.get("author") or {}).get("displayName") or "Unknown")
            date = (c.get("created") or "")[:10]
            body = adf_to_text(c.get("body"))
            if body:
                comment_parts.append(f"[{author}, {date}]\n{body}")
        if comment_parts:
            comments_text = compact_text(*comment_parts)
            last_updated = max((c.get("updated") or c.get("created") or "") for c in comments)
            comment_doc_id = db.upsert_document(
                {
                    "test_key": record["key"],
                    "source_type": "jira_comments",
                    "source_id": f"{record['key']}:comments",
                    "source_url": record["jira_url"],
                    "title": f"{record['summary']} — comments",
                    "text": comments_text,
                    "raw_json": json_dumps(comments),
                    "updated_at": last_updated,
                }
            )
            db.replace_chunks(comment_doc_id, record["key"], chunk_text(comments_text))
            log(f"  Stored {len(comment_parts)} comment(s).")

    # Gate on the results/learnings fields only, not on comment presence: many old
    # issues carry general discussion comments (assignments, process chatter) with
    # no actual results in them, and requiring comments to be empty too would skip
    # PDF extraction on exactly the tests it's meant to cover.
    has_results_text = bool(record["results"]) or bool(record["learnings"])
    if not has_results_text and not run.pdf_limit_reached:
        pdf_attachments = select_pdf_attachments((issue.get("fields") or {}).get("attachment"))
        # Results/learnings never fill in retroactively for these old issues, so once an
        # attachment is parsed there is nothing to gain by re-downloading and re-running
        # pdfplumber on it every sync.
        pdf_attachments = [
            a for a in pdf_attachments
            if not db.document_exists(record["key"], "jira_attachment_pdf", str(a.get("id")))
        ]
        if pdf_attachments:
            log(f"  No results text found; extracting {len(pdf_attachments)} PDF attachment(s).")
        for attachment in pdf_attachments:
            filename = attachment.get("filename") or f"attachment-{attachment.get('id')}"
            content_url = attachment.get("content")
            if not content_url:
                continue
            try:
                pdf_bytes = client.get_attachment_bytes(content_url)
                pdf_text, labeled = pdf_to_text(pdf_bytes)
            except Exception as exc:
                log(f"    Could not extract {filename}: {exc}")
                continue
            if not pdf_text.strip():
                log(f"    {filename}: no extractable text (likely a scanned/image PDF).")
                continue
            coverage = "with labeled Control/Variation breakdown" if labeled else "plain text only, no Control/Variation layout detected"
            attachment_doc_id = db.upsert_document(
                {
                    "test_key": record["key"],
                    "source_type": "jira_attachment_pdf",
                    "source_id": str(attachment.get("id")),
                    "source_url": content_url,
                    "title": f"{record['summary']} — {filename}",
                    "text": pdf_text,
                    "raw_json": json_dumps(
                        {
                            "filename": filename,
                            "mimeType": attachment.get("mimeType"),
                            "size": attachment.get("size"),
                        }
                    ),
                    "updated_at": attachment.get("created"),
                }
            )
            db.replace_chunks(attachment_doc_id, record["key"], chunk_text(pdf_text))
            log(f"    Extracted {len(pdf_text)} characters from {filename} ({coverage}).")
            run.reports += 1
            run.pdfs += 1
            if run.pdf_limit and run.pdfs >= pdf_limit:
                run.pdf_limit_reached = True
                log(f"  Reached --pdf-limit of {run.pdf_limit}; skipping PDF extraction for the rest of this run.")
                break


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
