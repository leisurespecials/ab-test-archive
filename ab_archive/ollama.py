from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaClient:
    def __init__(self, base_url: str, chat_model: str, embed_model: str):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model

    def embed(self, text: str) -> list[float]:
        data = self._post("/api/embed", {"model": self.embed_model, "input": text})
        embeddings = data.get("embeddings") or []
        if not embeddings:
            return []
        return [float(value) for value in embeddings[0]]

    def chat(self, messages: list[dict[str, str]]) -> str:
        data = self._post(
            "/api/chat",
            {"model": self.chat_model, "messages": messages, "stream": False},
            timeout=180,
        )
        return ((data.get("message") or {}).get("content") or "").strip()

    def chat_json(self, messages: list[dict[str, str]]) -> str:
        """Chat with deterministic settings and a reply constrained to one JSON object."""
        data = self._post(
            "/api/chat",
            {
                "model": self.chat_model,
                "messages": messages,
                "stream": False,
                "format": "json",
                # num_predict: in JSON mode the model can otherwise emit whitespace until the
                # request times out on some inputs; a truncated reply is simply discarded.
                "options": {"temperature": 0, "seed": 0, "num_predict": 500},
            },
            timeout=180,
        )
        return ((data.get("message") or {}).get("content") or "").strip()

    def _post(self, path: str, payload: dict[str, Any], timeout: int = 120) -> Any:
        req = Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Ollama is running but refused or aborted this request (e.g. "prediction aborted,
            # token repeat limit reached"); say so instead of claiming it is unreachable.
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Ollama returned {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(
                "Could not reach Ollama. Start Ollama and pull the configured models first."
            ) from exc
