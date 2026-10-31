# -*- coding: utf-8 -*-
"""Phase 1 contract tests — no DSH, no NetEase network, no Cognitive Core."""

from __future__ import annotations

import asyncio
import unittest

from music_acquisition.domain.models import (
    AuthorizationRequest,
    MusicScopeConfig,
    SyncRequest,
)
from test_audit_compliance import consent
from music_acquisition.engine import MusicAcquisitionEngine
from music_acquisition.hosts.in_memory import InMemoryHostRuntime
from music_acquisition.util.runtime import local_date
from music_acquisition.snapshot.hashing import snapshot_hash
from music_acquisition.snapshot.manager import SnapshotManager
from music_acquisition.source.mock_adapter import MockNeteaseAdapter


class EngineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = InMemoryHostRuntime()
        self.engine = MusicAcquisitionEngine(self.host, source=MockNeteaseAdapter(liked_count=4), owner_user_id="u1")

    def test_initialize_without_dsh(self) -> None:
        st = asyncio.run(self.engine.initialize())
        self.assertTrue(st.ok)
        self.assertEqual(st.version, "0.5.0")
        self.assertTrue(st.capabilities.features.get("likedSongs"))
        self.assertEqual(st.host, "in_memory")

    def test_auth_scopes_sync_snapshot_evidence(self) -> None:
        async def _run() -> None:
            await self.engine.initialize()
            ch = await self.engine.authorize(
                AuthorizationRequest(owner_user_id="u1", node_id="n1")
            )
            self.assertTrue(ch.session_id)
            auth = await self.engine.get_authorization_status(ch.session_id)
            self.assertEqual(auth.status, "authorized")
            self.assertEqual(auth.subject_id, "mock_uid_1")

            # scopes default off → sync denied
            run0 = await self.engine.sync_now(
                SyncRequest(owner_user_id="u1", collectors=["liked_songs"])
            )
            self.assertEqual(run0.status, "failed")
            self.assertEqual(run0.error_code, "SCOPE_DENIED")

            await self.engine.configure_scopes(
                MusicScopeConfig(
                    owner_user_id="u1",
                    scopes=["music.library.liked_songs", "music.recommend.daily_songs"],
                )
            )
            await consent(self.engine)
            run = await self.engine.sync_now(
                SyncRequest(owner_user_id="u1", collectors=["liked_songs", "daily_songs"])
            )
            self.assertEqual(run.status, "success")
            self.assertGreaterEqual(len(run.snapshot_ids), 1)

            snaps = await self.engine.get_snapshots(
                data_type="liked_songs", subject_id="mock_uid_1"
            )
            self.assertEqual(len(snaps), 1)
            self.assertTrue(snaps[0].complete)
            self.assertEqual(snaps[0].actual_count, 4)
            self.assertEqual(snaps[0].snapshot_hash, snapshot_hash(snaps[0].records))

            # daily → recommendation_seen; must not allow_create_memory by default
            daily = await self.engine.get_snapshots(
                data_type="daily_songs",
                subject_id="mock_uid_1",
                local_date=local_date(self.host.clock),
            )
            self.assertTrue(daily)
            ev = self.engine.map_to_evidence(daily[0])
            self.assertTrue(ev)
            self.assertEqual(ev[0]["evidence_type"], "music.recommendation_seen")
            self.assertFalse(ev[0]["policy"]["allow_create_memory"])

            # credentials never appear in logger lines as cookie-like blobs
            joined = "\n".join(self.host.logger.lines)
            self.assertNotIn("cookie", joined.lower())

            await self.engine.shutdown()

        asyncio.run(_run())

    def test_partial_does_not_overwrite_complete(self) -> None:
        mgr = SnapshotManager(self.host)
        snap = mgr.commit(
            owner_user_id="u1",
            data_type="liked_songs",
            subject_id="s1",
            records=[{"provider_track_id": "1", "title": "A"}],
            complete=True,
            expected_count=1,
        )
        self.assertIsNotNone(snap)
        rejected = mgr.commit(
            owner_user_id="u1",
            data_type="liked_songs",
            subject_id="s1",
            records=[{"provider_track_id": "1"}],
            complete=False,
            expected_count=10,
        )
        self.assertIsNone(rejected)
        cur = mgr.load("liked_songs", "s1")
        self.assertTrue(cur and cur.complete)
        self.assertEqual(cur.snapshot_id, snap.snapshot_id)


if __name__ == "__main__":
    unittest.main()
