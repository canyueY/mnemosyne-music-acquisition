# -*- coding: utf-8 -*-
"""Optional live Node contract test.

Skipped unless both env vars are set:
  MNEMOSYNE_BASE_URL
  DSH_MEMORY_TOKEN  (or MNEMOSYNE_TOKEN)
"""

from __future__ import annotations

import os
import unittest

from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient


@unittest.skipUnless(
    bool((os.environ.get("MNEMOSYNE_BASE_URL") or "").strip())
    and bool((os.environ.get("DSH_MEMORY_TOKEN") or os.environ.get("MNEMOSYNE_TOKEN") or "").strip()),
    "live Node env not configured",
)
class LiveMnemosyneContractTests(unittest.TestCase):
    def setUp(self) -> None:
        base = os.environ["MNEMOSYNE_BASE_URL"].rstrip("/")
        token = os.environ.get("DSH_MEMORY_TOKEN") or os.environ.get("MNEMOSYNE_TOKEN") or ""
        self.client = MnemosyneHttpClient(base, token)

    def test_contract_probe(self) -> None:
        probe = self.client.contract_probe()
        self.assertTrue(probe.get("ok"), probe)
        self.assertTrue(probe.get("search", {}).get("ok"), probe.get("search"))
        self.assertEqual(probe.get("search", {}).get("path"), "/api/v1/memories/search")
        # domain may already be registered from prior bootstrap
        self.assertTrue(
            probe.get("domain_get", {}).get("ok")
            or probe.get("domains_register_route", {}).get("exists"),
            probe,
        )

    def test_search_domain_filter_returns_music_items(self) -> None:
        hit = self.client.search("music", limit=5, domain="music")
        self.assertTrue(hit.get("ok"), hit)
        for it in hit.get("items") or []:
            meta = it.get("metadata") or {}
            d = str(meta.get("domain") or "")
            if d:
                self.assertIn(d, ("music", "domain:music"))


if __name__ == "__main__":
    unittest.main()
