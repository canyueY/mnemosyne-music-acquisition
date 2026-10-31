# -*- coding: utf-8 -*-
"""Thin HTTP client for Mnemosyne Service Contract (no sqlite)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class MnemosyneHttpClient:
    def __init__(self, base_url: str, token: str = "", *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {"ok": True}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            return {"ok": False, "error": f"http_{exc.code}", "detail": detail}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "network", "detail": str(exc)}

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/health")

    def observe(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/observations", payload)

    def propose_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/memories", payload)

    def update_memory(self, memory_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/api/v1/memories/{memory_id}", payload)

    def link_relation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/relations", payload)

    def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/feedback", payload)

    def list_relations(self, memory_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/memories/{memory_id}/relations")

    def register_domain_pack(self, pack: dict[str, Any] | None = None, *, pack_path: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"actor": "music_acquisition"}
        if pack_path:
            body["pack_path"] = pack_path
        elif pack:
            body["pack"] = pack
        return self._request("POST", "/api/v1/domains/register", body)

    def search(self, query: str, *, limit: int = 10, domain: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if domain:
            body["domain"] = domain
        return self._request("POST", "/api/v1/search", body)
