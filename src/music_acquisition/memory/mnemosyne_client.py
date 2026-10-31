# -*- coding: utf-8 -*-
"""Thin HTTP client for Mnemosyne Service Contract (no sqlite).

Mnemosyne 3.x contract notes (verified against live OpenAPI):
- Search is ``POST /api/v1/memories/search`` (not ``/api/v1/search``).
- Domain packs: ``POST /api/v1/domains/register`` (+ ``GET /api/v1/domains/{id}``).
- ``SearchQuery.domain`` is accepted; client still filters by ``metadata.domain`` as a safety net.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class MnemosyneHttpClient:
    # Preferred + legacy aliases (first hit wins).
    SEARCH_PATHS = (
        "/api/v1/memories/search",
        "/api/v1/search",  # legacy / mistaken path — kept as fallback only
    )
    DOMAIN_REGISTER_PATH = "/api/v1/domains/register"

    def __init__(self, base_url: str, token: str = "", *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
            h["X-Memory-Token"] = self.token
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
            return {"ok": False, "error": f"http_{exc.code}", "detail": detail, "path": path}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "network", "detail": str(exc), "path": path}

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

    def get_domain(self, domain_id: str = "music") -> dict[str, Any]:
        did = str(domain_id or "music").removeprefix("domain:")
        return self._request("GET", f"/api/v1/domains/{did}")

    def register_domain_pack(self, pack: dict[str, Any] | None = None, *, pack_path: str = "") -> dict[str, Any]:
        """Register Music Domain Pack.

        Always prefer inline ``pack`` JSON. If only ``pack_path`` is given, load it
        locally first — Node cannot read paths on the music-acquisition host.
        """
        body: dict[str, Any] = {"actor": "music_acquisition"}
        inline = pack
        path = str(pack_path or "").strip()
        if inline is None and path:
            p = Path(path)
            if p.is_file():
                try:
                    inline = json.loads(p.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": "pack_read_failed", "detail": str(exc), "pack_path": path}
            else:
                # Last resort: ask Node to resolve path (only works if same machine + Node can read it).
                body["pack_path"] = path
        if inline is not None:
            body["pack"] = inline
            # Also keep path as audit hint when available.
            if path:
                body["pack_path_hint"] = path
        if "pack" not in body and "pack_path" not in body:
            return {"ok": False, "error": "pack_or_path_required"}

        result = self._request("POST", self.DOMAIN_REGISTER_PATH, body)
        if result.get("error") == "http_404":
            return {
                "ok": False,
                "error": "domains_register_unsupported",
                "detail": result.get("detail"),
                "path": self.DOMAIN_REGISTER_PATH,
                "note": (
                    "Node has no POST /api/v1/domains/register. "
                    "Upgrade Mnemosyne Node, or treat Domain Pack as local-only ontology."
                ),
                "pack_domain_id": (inline or {}).get("domain_id") if isinstance(inline, dict) else None,
            }
        # Normalize success shapes.
        if result.get("error"):
            return result
        domain_id = "music"
        if isinstance(inline, dict):
            domain_id = str(inline.get("domain_id") or "music")
        verified = self.get_domain(domain_id)
        verified_ok = (
            isinstance(verified, dict)
            and not verified.get("error")
            and bool(verified.get("id") or verified.get("domain_id"))
        )
        ok = not result.get("error") and (
            result.get("ok", True) is not False
            or "id" in result
            or "domain" in result
            or verified_ok
        )
        return {
            "ok": bool(ok),
            "register": result,
            "verified": verified if verified_ok else None,
            "domain_id": domain_id,
        }

    @staticmethod
    def _extract_items(payload: dict[str, Any]) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        for key in ("items", "memories", "results", "hits"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("items", "memories", "results"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
        return []

    @staticmethod
    def _filter_domain(items: list[Any], domain: str | None) -> list[Any]:
        if not domain:
            return items
        want = {domain, f"domain:{domain}"}
        has_domain_meta = False
        kept: list[Any] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            meta = it.get("metadata") if isinstance(it.get("metadata"), dict) else {}
            d = str(meta.get("domain") or "").strip()
            if d:
                has_domain_meta = True
                if d in want:
                    kept.append(it)
            else:
                # No domain tag — keep for now; strict filter applied only when corpus is tagged.
                kept.append(it)
        if not has_domain_meta:
            return items
        return kept

    def search(self, query: str, *, limit: int = 10, domain: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "limit": int(limit)}
        if domain:
            body["domain"] = domain

        last: dict[str, Any] = {"ok": False, "error": "search_path_exhausted"}
        used_path = ""
        for path in self.SEARCH_PATHS:
            last = self._request("POST", path, body)
            used_path = path
            if last.get("error") == "http_404":
                continue
            break

        if last.get("error"):
            return {**last, "path": used_path, "tried": list(self.SEARCH_PATHS)}

        items = self._extract_items(last)
        items = self._filter_domain(items, domain)
        return {
            "ok": True,
            "items": items,
            "count": len(items),
            "path": used_path,
            "domain": domain,
            "raw": last,
        }

    def propose_claim(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/claims when Node supports Claim objects (optional)."""
        return self._request("POST", "/api/v1/claims", payload)

    def contract_probe(self) -> dict[str, Any]:
        """Lightweight capability probe for doctor / bootstrap diagnostics."""
        health = self.health()
        search = self.search("music", limit=1, domain="music")
        domain = self.get_domain("music")
        reg_probe = self._request("POST", self.DOMAIN_REGISTER_PATH, {"actor": "music_acquisition"})
        claims_probe = self._request("POST", "/api/v1/claims", {"actor": "music_acquisition"})
        return {
            "ok": bool(health.get("ok", True) is not False and not health.get("error")),
            "health": health,
            "search": {
                "ok": bool(search.get("ok")),
                "path": search.get("path"),
                "error": search.get("error"),
            },
            "domain_get": {
                "ok": not domain.get("error") and bool(domain.get("id") or domain.get("domain_id")),
                "error": domain.get("error"),
                "id": domain.get("id") or domain.get("domain_id"),
            },
            "domains_register_route": {
                "exists": reg_probe.get("error") != "http_404",
                "error": reg_probe.get("error"),
                "note": "empty body may 422; 404 means route missing",
            },
            "claims_route": {
                "exists": claims_probe.get("error") != "http_404",
                "error": claims_probe.get("error"),
                "note": "404 → ClaimBridge uses Memory fallback (data_kind=claim)",
            },
        }
