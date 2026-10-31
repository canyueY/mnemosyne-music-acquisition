# -*- coding: utf-8 -*-
"""Contract path tests against a fake Mnemosyne HTTP surface (no live Node)."""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from typing import Any
from unittest.mock import patch

from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient
from music_acquisition.memory.recall import MusicRecallService


class _FakeResp:
    def __init__(self, status: int, body: dict[str, Any] | str) -> None:
        self.status = status
        if isinstance(body, dict):
            raw = json.dumps(body).encode("utf-8")
        else:
            raw = str(body).encode("utf-8")
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _HTTPError(Exception):
    def __init__(self, code: int, body: bytes = b"") -> None:
        self.code = code
        self._body = body

    def read(self) -> bytes:
        return self._body


class TestMnemosyneContractPaths(unittest.TestCase):
    def test_search_uses_memories_search_not_legacy(self) -> None:
        seen: list[str] = []

        def fake_urlopen(req: Any, timeout: float = 0):  # noqa: ANN401
            path = req.full_url.replace("http://node", "")
            seen.append(path)
            if path == "/api/v1/search":
                import urllib.error

                err = urllib.error.HTTPError(
                    req.full_url, 404, "Not Found", hdrs=None, fp=BytesIO(b'{"detail":"Not Found"}')
                )
                raise err
            if path == "/api/v1/memories/search":
                return _FakeResp(
                    200,
                    {
                        "items": [
                            {
                                "id": "m1",
                                "content": "[music:observed] 收藏了歌曲《x》(123)",
                                "metadata": {"domain": "music", "target_id": "123", "action": "saved"},
                            },
                            {
                                "id": "m2",
                                "content": "other",
                                "metadata": {"domain": "weather"},
                            },
                        ],
                        "count": 2,
                    },
                )
            raise AssertionError(f"unexpected path {path}")

        client = MnemosyneHttpClient("http://node", "tok")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = client.search("123", limit=5, domain="music")

        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("path"), "/api/v1/memories/search")
        self.assertEqual([i["id"] for i in out["items"]], ["m1"])
        self.assertIn("/api/v1/memories/search", seen)
        self.assertNotIn("/api/v1/search", seen)  # first path succeeds; legacy unused

    def test_search_falls_back_when_memories_search_404(self) -> None:
        import urllib.error

        def fake_urlopen(req: Any, timeout: float = 0):  # noqa: ANN401
            path = req.full_url.replace("http://node", "")
            if path == "/api/v1/memories/search":
                raise urllib.error.HTTPError(
                    req.full_url, 404, "Not Found", hdrs=None, fp=BytesIO(b"{}")
                )
            if path == "/api/v1/search":
                return _FakeResp(200, {"items": [{"id": "legacy", "metadata": {"domain": "music"}}]})
            raise AssertionError(path)

        client = MnemosyneHttpClient("http://node", "tok")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = client.search("q", domain="music")
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("path"), "/api/v1/search")

    def test_register_domain_404_is_honest(self) -> None:
        import urllib.error

        def fake_urlopen(req: Any, timeout: float = 0):  # noqa: ANN401
            raise urllib.error.HTTPError(
                req.full_url, 404, "Not Found", hdrs=None, fp=BytesIO(b'{"detail":"Not Found"}')
            )

        client = MnemosyneHttpClient("http://node", "tok")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = client.register_domain_pack({"domain_id": "music", "version": "1.0.0"})
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "domains_register_unsupported")

    def test_register_domain_prefers_inline_and_verifies(self) -> None:
        calls: list[tuple[str, dict | None]] = []

        def fake_urlopen(req: Any, timeout: float = 0):  # noqa: ANN401
            path = req.full_url.replace("http://node", "")
            body = None
            if req.data:
                body = json.loads(req.data.decode("utf-8"))
            calls.append((path, body))
            if path == "/api/v1/domains/register":
                self.assertIn("pack", body or {})
                self.assertNotIn("pack_path", body or {})
                return _FakeResp(200, {"ok": True, "id": "domain:music"})
            if path == "/api/v1/domains/music":
                return _FakeResp(200, {"id": "domain:music", "domain_id": "music"})
            raise AssertionError(path)

        client = MnemosyneHttpClient("http://node", "tok")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = client.register_domain_pack({"domain_id": "music", "version": "1.0.0"})
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("domain_id"), "music")
        self.assertIsNotNone(out.get("verified"))

    def test_recall_passes_domain_music(self) -> None:
        class _Stub:
            def search(self, query: str, *, limit: int = 10, domain: str | None = None) -> dict:
                return {"ok": True, "items": [{"id": "x"}], "path": "/api/v1/memories/search", "domain": domain}

        class _Host:
            pass

        svc = MusicRecallService(_Host(), mnemosyne=_Stub())  # type: ignore[arg-type]
        out = svc.recall_with_mnemosyne("喜欢什么歌")
        self.assertTrue(out["ok"])
        self.assertEqual(out["raw"]["domain"], "music")


if __name__ == "__main__":
    unittest.main()
