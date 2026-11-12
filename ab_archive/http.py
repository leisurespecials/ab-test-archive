from __future__ import annotations

from base64 import b64encode
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# Transient failures worth retrying: rate limiting and server-side errors. Jira regularly
# answers a single request with a 500 ("SSL error: Read timed out") that succeeds on retry.
RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(
        self,
        base_url: str,
        email: str | None = None,
        token: str | None = None,
        *,
        retries: int = 4,
        backoff: float = 2.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.token = token
        self.retries = retries
        self.backoff = backoff

    def _send(self, req: Request, method: str, url: str, timeout: int) -> bytes:
        """Send a request, retrying transient failures with exponential backoff."""
        attempt = 0
        while True:
            try:
                with urlopen(req, timeout=timeout) as response:
                    return response.read()
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in RETRY_STATUSES and attempt < self.retries:
                    self._wait(attempt, exc.headers.get("Retry-After") if exc.headers else None)
                    attempt += 1
                    continue
                raise RuntimeError(f"{method} {url} failed with {exc.code}: {detail}") from exc
            except OSError as exc:
                # URLError (DNS, refused connection) and socket timeouts while reading the body.
                if attempt < self.retries:
                    self._wait(attempt, None)
                    attempt += 1
                    continue
                reason = exc.reason if isinstance(exc, URLError) else exc
                raise RuntimeError(f"{method} {url} failed: {reason}") from exc

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        delay = self.backoff * (2**attempt)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        time.sleep(min(delay, 60.0))

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.email and self.token:
            raw = f"{self.email}:{self.token}".encode("utf-8")
            headers["Authorization"] = "Basic " + b64encode(raw).decode("ascii")
        req = Request(url, data=data, method=method.upper(), headers=headers)
        payload = self._send(req, method, url, timeout)
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    def download(self, url: str, *, timeout: int = 60) -> bytes:
        headers = {}
        if self.email and self.token:
            raw = f"{self.email}:{self.token}".encode("utf-8")
            headers["Authorization"] = "Basic " + b64encode(raw).decode("ascii")
        req = Request(url, method="GET", headers=headers)
        return self._send(req, "GET", url, timeout)
