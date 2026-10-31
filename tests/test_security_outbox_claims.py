# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from music_acquisition.hosts.filesystem import FileCredentialStore, FilesystemHostRuntime
from music_acquisition.hosts.scheduler import ThreadingScheduler
from music_acquisition.memory.bank_backend import LocalFileBankBackend
from music_acquisition.memory.claims import ClaimBridge, build_preference_claim
from music_acquisition.memory.outbox import EvidenceOutbox
from music_acquisition.memory.submit import EvidenceSubmitter
from music_acquisition.security.credential_crypto import is_encrypted_blob


class SecurityOutboxClaimsTests(unittest.TestCase):
    def test_credential_encrypted_at_rest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = FileCredentialStore(Path(td))

            async def _run() -> None:
                plain = b"MUSIC_U=abc; NMTID=xyz"
                await store.save("owner", plain)
                raw = (Path(td) / "credentials" / "owner.bin").read_bytes()
                self.assertTrue(is_encrypted_blob(raw))
                self.assertNotIn(b"MUSIC_U=", raw)
                loaded = await store.load("owner")
                self.assertEqual(loaded, plain)
                # legacy plaintext upgrade
                legacy = Path(td) / "credentials" / "legacy.bin"
                legacy.write_bytes(b"cookie=plain")
                got = await store.load("legacy")
                self.assertEqual(got, b"cookie=plain")
                self.assertTrue(is_encrypted_blob(legacy.read_bytes()))

            asyncio.run(_run())

    def test_outbox_per_item_partial_submit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            host = FilesystemHostRuntime(td)
            outbox = EvidenceOutbox(host)
            oid = outbox.enqueue(
                [
                    {"evidence_id": "a", "subject_id": "1", "action": "saved", "data_kind": "user_action"},
                    {"evidence_id": "b", "subject_id": "2", "action": "saved", "data_kind": "user_action"},
                ]
            )

            class _Client:
                def __init__(self) -> None:
                    self.n = 0

                def observe(self, payload):  # noqa: ANN001
                    self.n += 1
                    if self.n == 1:
                        return {"ok": True, "id": "obs1"}
                    return {"ok": False, "error": "network"}

            sub = EvidenceSubmitter(host, _Client(), outbox)
            result = sub.submit_pending()
            self.assertTrue(result.get("per_item"))
            batch = outbox.load(oid)
            assert batch is not None
            statuses = [i.get("item_status") for i in batch["items"]]
            self.assertEqual(statuses[0], "submitted")
            self.assertEqual(statuses[1], "pending")
            self.assertEqual(batch.get("status"), "partial")
            # second pass should not re-submit first item
            client2 = _Client()
            client2.n = 0

            class _Ok:
                def observe(self, payload):  # noqa: ANN001
                    return {"ok": True, "id": "obs2"}

            # force=True bypasses next_retry_at backoff for immediate recovery
            result2 = EvidenceSubmitter(host, _Ok(), outbox).submit_pending(force=True)
            batch2 = outbox.load(oid)
            assert batch2 is not None
            self.assertEqual(batch2.get("status"), "submitted")
            self.assertTrue(all(i.get("item_status") == "submitted" for i in batch2["items"]))
            self.assertTrue(any(r.get("submitted_now") == 1 for r in result2.get("results") or []))

    def test_claim_bridge_memory_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bank = LocalFileBankBackend(Path(td) / "bank")
            bridge = ClaimBridge(client=None, bank=bank)
            out = bridge.confirm_like_claim(target_type="track", target_id="99", title="x")
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("backend"), "memory_fallback")
            mid = out.get("memory_id")
            doc = bank.search("music:claim", limit=5, domain="music")
            self.assertTrue(any(str(i.get("id")) == mid for i in doc.get("items") or []))

    def test_scheduler_fires(self) -> None:
        sched = ThreadingScheduler()
        box: list[int] = []
        sched.schedule("t1", 0.05, lambda: box.append(1))
        import time

        time.sleep(0.2)
        self.assertEqual(box, [1])
        sched.shutdown()

    def test_build_claim_shape(self) -> None:
        c = build_preference_claim(
            target_type="track",
            target_id="1",
            epistemic="inferred",
            hypothesis="maybe likes",
        )
        self.assertEqual(c["modality"], "preference")
        self.assertEqual(c["domain"], "music")
        self.assertTrue(c["policy"]["must_not_write_likes"])


if __name__ == "__main__":
    unittest.main()
