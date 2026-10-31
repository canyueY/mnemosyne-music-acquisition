# -*- coding: utf-8 -*-
"""Phases 2–6 integration with Mock + config-bind adapter (no live NetEase required)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from music_acquisition.domain.models import AuthorizationRequest, MusicScopeConfig, SyncRequest
from test_audit_compliance import consent
from music_acquisition.engine import MusicAcquisitionEngine
from music_acquisition.hosts.filesystem import FilesystemHostRuntime
from music_acquisition.source.netease.adapter import NeteaseSourceAdapter


class PhaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        # A verified synthetic account; public playlist binding alone is not login.
        account = mock.patch("music_acquisition.source.netease.qr_auth.fetch_account", return_value={"code": 200, "userId": "42"})
        account.start()
        self.addCleanup(account.stop)

    def test_config_bind_and_mocked_collectors(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                host = FilesystemHostRuntime(tmp)
                adapter = NeteaseSourceAdapter(cookie="SYNTHETIC_ONLY", subject_id="42")
                engine = MusicAcquisitionEngine(host, source=adapter, owner_user_id="u1")
                await engine.initialize()
                ch = await engine.authorize(AuthorizationRequest(owner_user_id="u1", mode="config_bind"))
                # QR unsupported path — complete via config bind
                st = await engine.complete_config_bind(ch.session_id, favorite_playlist_id="123456")
                self.assertEqual(st.status, "authorized")

                await engine.configure_scopes(
                    MusicScopeConfig(
                        owner_user_id="u1",
                        scopes=["music.library.liked_songs", "music.recommend.daily_songs"],
                    )
                )

                await consent(engine)
                fake_tracks = [
                    {
                        "track_id": "netease:1",
                        "provider": "netease",
                        "provider_track_id": "1",
                        "title": "A",
                        "artists": [],
                        "metadata": {},
                    }
                ]

                with mock.patch(
                    "music_acquisition.source.netease.adapter.api.fetch_playlist_tracks",
                    return_value=(fake_tracks, 1, True),
                ), mock.patch(
                    "music_acquisition.source.netease.adapter.qr_auth._request",
                    return_value=({"code": 200, "data": {"dailySongs": [{"id": 1, "name": "A"}]}}, {}),
                ):
                    run = await engine.sync_now(
                        SyncRequest(
                            owner_user_id="u1",
                            collectors=["liked_songs", "daily_songs"],
                        )
                    )
                self.assertEqual(run.status, "success")
                snaps = await engine.get_snapshots(data_type="liked_songs", subject_id="42")
                self.assertEqual(len(snaps), 1)
                recall = engine.recall(subject_id="42", query="喜欢")
                self.assertTrue(recall["claims"])
                # outbox filled
                pending = Path(tmp) / "memory" / "outbox" / "_index.json"
                self.assertTrue(pending.is_file())
                # playback without murasame → unsupported
                pb = engine.playback.play_song("1")
                # 无桌宠桥接时失败；本仓与桌宠同树时允许 ok
                if not pb.get("ok"):
                    self.assertIn(pb.get("error"), ("PLAYBACK_UNSUPPORTED", "PLAY_FAILED", "PLAYBACK_PERMISSION_DENIED"))
                else:
                    self.assertTrue(pb.get("result"))
                await engine.shutdown()

        asyncio.run(_run())

    def test_partial_playlist_does_not_clobber(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                host = FilesystemHostRuntime(tmp)
                adapter = NeteaseSourceAdapter(favorite_playlist_id="9", cookie="SYNTHETIC_ONLY", subject_id="42")
                engine = MusicAcquisitionEngine(host, source=adapter, owner_user_id="u1")
                await engine.initialize()
                await engine.configure_scopes(
                    MusicScopeConfig(owner_user_id="u1", scopes=["music.library.liked_songs"])
                )
                await consent(engine)
                full = [{"provider_track_id": str(i), "title": str(i)} for i in range(3)]
                with mock.patch(
                    "music_acquisition.source.netease.adapter.api.fetch_playlist_tracks",
                    return_value=(full, 3, True),
                ):
                    await engine.sync_now(
                        SyncRequest(owner_user_id="u1", collectors=["liked_songs"])
                    )
                with mock.patch(
                    "music_acquisition.source.netease.adapter.api.fetch_playlist_tracks",
                    return_value=(full[:1], 3, False),
                ):
                    await engine.sync_now(
                        SyncRequest(owner_user_id="u1", collectors=["liked_songs"])
                    )
                snap = (await engine.get_snapshots(data_type="liked_songs", subject_id="42"))[0]
                self.assertTrue(snap.complete)
                self.assertEqual(snap.actual_count, 3)
                await engine.shutdown()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
