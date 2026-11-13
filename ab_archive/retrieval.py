from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
import math
import re

from .db import ArchiveDB, SearchResult
from .ollama import OllamaClient


def chunk_text(text: str, max_chars: int = 1400, overlap: int = 180) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start + max_chars // 2:
                end = boundary
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def embed_missing(db: ArchiveDB, ollama: OllamaClient, progress: Callable[[str], None] | None = None) -> int:
    count = 0
    rows = db.chunks_without_embeddings()
    total = len(rows)
    if progress:
        progress(f"Embedding {total} new/updated text chunks...")
    for index, row in enumerate(rows, start=1):
        if progress and (index == 1 or index % 25 == 0 or index == total):
            progress(f"Embedding chunk {index}/{total}...")
        embedding = ollama.embed(row["text"])
        if embedding:
            db.set_chunk_embedding(int(row["id"]), embedding)
            count += 1
    db.commit()
    return count


def hybrid_search(db: ArchiveDB, query: str, ollama: OllamaClient | None, limit: int = 8) -> list[SearchResult]:
    combined: dict[tuple[str, str, str], SearchResult] = {}
    for result in _metadata_results(db, query, limit):
        combined[(result.key, result.source_url, result.text)] = result
    try:
        for result in db.keyword_search(_fts_query(query), limit=limit):
            combined[(result.key, result.source_url, result.text)] = result
    except Exception:
        for result in db.keyword_search(_quoted_fts_query(query), limit=limit):
            combined[(result.key, result.source_url, result.text)] = result

    if ollama:
        try:
            query_embedding = ollama.embed(query)
        except RuntimeError:
            query_embedding = []
        if query_embedding:
            semantic = []
            for row in db.all_embedded_chunks():
                embedding = json.loads(row["embedding_json"])
                score = cosine_similarity(query_embedding, embedding)
                semantic.append(
                    SearchResult(
                        key=row["test_key"],
                        title=row["summary"],
                        status=row["status"],
                        source_type=row["source_type"],
                        source_url=row["source_url"],
                        text=row["text"],
                        score=score,
                    )
                )
            for result in sorted(semantic, key=lambda item: item.score, reverse=True)[:limit]:
                key = (result.key, result.source_url, result.text)
                existing = combined.get(key)
                combined[key] = replace(result, score=result.score + (existing.score if existing else 0))

    return sorted(combined.values(), key=lambda item: item.score, reverse=True)[:limit]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _fts_query(query: str) -> str:
    terms = _query_terms(query)
    return " OR ".join(f'"{term}"' for term in terms) or '""'


def _quoted_fts_query(query: str) -> str:
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


def _query_terms(query: str) -> list[str]:
    stopwords = {
        "a", "an", "and", "are", "by", "did", "do", "for", "in", "of", "on",
        "the", "to", "we", "were", "what", "which", "with",
    }
    terms = []
    for term in re.findall(r"[A-Za-z0-9]+", query.lower()):
        if len(term) < 2 or term in stopwords:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _metadata_results(db: ArchiveDB, query: str, limit: int) -> list[SearchResult]:
    text = query.lower()
    status = None
    statuses = None
    if "no significant" in text:
        status = "No significant Result"
    elif "unsuccessful" in text or "lost" in text or "losing" in text:
        status = "Unsuccessful"
    elif "successful" in text or "winner" in text or "winning" in text:
        statuses = ["Successful", "Implemented"]
    elif "implemented" in text:
        status = "Implemented"

    page_type = None
    for candidate in ["checkout", "property", "funnel", "lister", "search", "homepage", "campaign", "website"]:
        if candidate in text:
            page_type = candidate
            break
    if "hotel page" in text:
        page_type = "property"
    if page_type == "checkout":
        page_type = "funnel"  # tickets name the checkout pages "Funnel"; no test is filed as checkout

    market = None
    for candidate in re.findall(r"\b[A-Z]{2,6}\b", query):
        if candidate.startswith(("HS", "BS", "FS", "VS")):
            market = candidate
            break
    return db.metadata_search(status=status, statuses=statuses, page_type=page_type, market=market, limit=limit)
