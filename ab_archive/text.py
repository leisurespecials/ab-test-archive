from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any


def adf_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return adf_to_text(json.loads(stripped))
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, list):
        return _clean("\n".join(adf_to_text(item) for item in value))
    if not isinstance(value, dict):
        return str(value)

    node_type = value.get("type")
    content = value.get("content") or []
    attrs = value.get("attrs") or {}

    if node_type == "text":
        text = value.get("text", "")
        for mark in value.get("marks") or []:
            if mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href")
                if href:
                    text = f"{text} ({href})"
        return text
    if node_type in {"hardBreak", "rule"}:
        return "\n"
    if node_type == "emoji":
        return attrs.get("shortName") or attrs.get("text") or ""
    if node_type == "inlineCard":
        return attrs.get("url", "")
    if node_type == "mediaSingle":
        return ""
    if node_type == "codeBlock":
        return "\n".join(adf_to_text(item) for item in content)
    if node_type == "listItem":
        return "- " + _clean(" ".join(adf_to_text(item) for item in content))
    if node_type in {"bulletList", "orderedList"}:
        return "\n".join(adf_to_text(item) for item in content)
    if node_type in {"paragraph", "heading", "blockquote", "panel"}:
        return _clean("".join(adf_to_text(item) for item in content)) + "\n"
    if node_type in {"table", "tableRow"}:
        return "\n".join(adf_to_text(item) for item in content)
    if node_type in {"tableCell", "tableHeader"}:
        return _clean(" ".join(adf_to_text(item) for item in content))

    return _clean("\n".join(adf_to_text(item) for item in content))


def html_to_text(html: str) -> str:
    parser = _HTMLTextParser()
    parser.feed(html or "")
    return _clean(parser.text())


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    urls = re.findall(r"https?://[^\s)\]\"'>]+", text)
    cleaned = []
    for url in urls:
        url = url.rstrip(".,;:")
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


def extract_confluence_page_ids(text: str) -> list[str]:
    ids = []
    for url in extract_urls(text):
        match = re.search(r"/wiki/(?:spaces/[^/]+/)?pages/(\d+)", url)
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def infer_market(summary: str) -> str:
    match = re.match(r"\s*([A-Z]{2,6})\s*-", summary or "")
    return match.group(1) if match else ""


def infer_page_type(summary: str, description: str) -> str:
    haystack = f"{summary} {description}".lower()
    candidates = [
        ("checkout", ["checkout"]),
        ("funnel", ["funnel", "step 1", "step1", "step 2", "booking overlay"]),
        ("property", ["property", "hotel page", "hotel pages"]),
        ("lister", ["lister", "search results", "destination landing"]),
        ("campaign", ["campaign"]),
        ("homepage", ["homepage", "home page"]),
        ("website", ["website"]),
    ]
    for page_type, needles in candidates:
        if any(needle in haystack for needle in needles):
            return page_type
    parts = [part.strip().lower() for part in (summary or "").split("-")]
    return parts[1] if len(parts) > 2 else ""


def compact_text(*parts: str) -> str:
    return _clean("\n\n".join(part for part in parts if part))


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text or "")).strip()


class _HTMLTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in {"p", "br", "li", "tr", "h1", "h2", "h3"}:
            self._parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._parts.append(f" {href} ")

    def handle_data(self, data: str):
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)
