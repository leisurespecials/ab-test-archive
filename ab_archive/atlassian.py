from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Iterator

from .config import JIRA_FIELDS, JQL_SCOPE
from .http import HttpClient
from .text import adf_to_text, html_to_text


@dataclass
class AtlassianClient:
    base_url: str
    email: str
    token: str

    def __post_init__(self) -> None:
        self.http = HttpClient(self.base_url, self.email, self.token)

    def iter_ab_test_issues(self, progress: Callable[[str], None] | None = None) -> Iterator[dict[str, Any]]:
        token: str | None = None
        page = 1
        while True:
            body: dict[str, Any] = {
                "jql": JQL_SCOPE + " ORDER BY updated DESC",
                "maxResults": 100,
                "fields": JIRA_FIELDS,
            }
            if token:
                body["nextPageToken"] = token
            if progress:
                progress(f"Fetching Jira page {page}...")
            data = self.http.request("POST", "/rest/api/3/search/jql", body=body)
            issues = data.get("issues", [])
            if progress:
                progress(f"Fetched {len(issues)} Jira work items from page {page}.")
            for issue in issues:
                yield issue
            token = data.get("nextPageToken")
            if data.get("isLast", not token):
                break
            page += 1

    def get_issue_comments(self, key: str) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        start_at = 0
        while True:
            data = self.http.request(
                "GET",
                f"/rest/api/3/issue/{key}/comment",
                params={"startAt": start_at, "maxResults": 100},
            )
            batch = data.get("comments") or []
            comments.extend(batch)
            if len(comments) >= int(data.get("total", 0)) or not batch:
                break
            start_at += len(batch)
        return comments

    def get_confluence_page(self, page_id: str) -> dict[str, Any]:
        data = self.http.request(
            "GET",
            f"/wiki/api/v2/pages/{page_id}",
            params={"body-format": "atlas_doc_format"},
        )
        body = ((data.get("body") or {}).get("atlas_doc_format") or {}).get("value")
        text = adf_to_text(body)
        if not text:
            storage = self.http.request(
                "GET",
                f"/wiki/api/v2/pages/{page_id}",
                params={"body-format": "storage"},
            )
            storage_value = ((storage.get("body") or {}).get("storage") or {}).get("value", "")
            text = html_to_text(storage_value)
            data = storage
        data["_archive_text"] = text
        return data

    def get_attachment_bytes(self, content_url: str) -> bytes:
        return self.http.download(content_url)


def issue_web_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/browse/{key}"


def page_web_url(base_url: str, page_id: str) -> str:
    return f"{base_url.rstrip('/')}/wiki/pages/{page_id}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
