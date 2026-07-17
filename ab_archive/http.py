from __future__ import annotations

from base64 import b64encode
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class HttpClient:
    def __init__(self, base_url: str, email: str | None = None, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.token = token

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
        try:
            with urlopen(req, timeout=timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {url} failed with {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    def download(self, url: str, *, timeout: int = 60) -> bytes:
        headers = {}
        if self.email and self.token:
            raw = f"{self.email}:{self.token}".encode("utf-8")
            headers["Authorization"] = "Basic " + b64encode(raw).decode("ascii")
        req = Request(url, method="GET", headers=headers)
        try:
            with urlopen(req, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GET {url} failed with {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"GET {url} failed: {exc.reason}") from exc
