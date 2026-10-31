# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from music_acquisition.hosts.in_memory import InMemoryHostRuntime
from music_acquisition.memory.adapter import MusicMemoryAdapter
from music_acquisition.memory.submit import _observation_metadata
from music_acquisition.domain.models import MusicSnapshot


class MusicDomainEvidenceTests(unittest.TestCase):
    def test_liked_songs_are_observed_user_action_not_like(self) -> None:
        host = InMemoryHostRuntime()
        adapter = MusicMemoryAdapter(host)
        snap = MusicSnapshot(
            snapshot_id="s1",
            owner_user_id="u1",
            data_type="liked_songs",
            subject_id="netease_local",
            fetched_at="2026-10-31T00:00:00Z",
            records=[{"provider_track_id": "1470902634", "title": "At Least I Had Fun"}],
        )
        items = adapter.map_snapshot_to_evidence(snap, allow_create_memory=True)
        self.assertEqual(len(items), 1)
        ev = items[0]
        self.assertEqual(ev["domain"], "music")
        self.assertEqual(ev["data_kind"], "user_action")
        self.assertEqual(ev["action"], "saved")
        self.assertEqual(ev["status"], "observed")
        self.assertTrue(ev["policy"]["allow_create_memory"])
        meta = _observation_metadata(ev, outbox_id="out1")
        self.assertEqual(meta["domain"], "music")
        self.assertEqual(meta["data_kind"], "user_action")

    def test_daily_recommendation_never_creates_memory(self) -> None:
        host = InMemoryHostRuntime()
        adapter = MusicMemoryAdapter(host)
        snap = MusicSnapshot(
            snapshot_id="s2",
            owner_user_id="u1",
            data_type="daily_songs",
            subject_id="netease_local",
            fetched_at="2026-10-31T00:00:00Z",
            records=[{"provider_track_id": "1", "title": "x"}],
        )
        items = adapter.map_snapshot_to_evidence(snap, allow_create_memory=True)
        self.assertEqual(items[0]["data_kind"], "catalog")
        self.assertEqual(items[0]["action"], "recommendation_seen")
        self.assertFalse(items[0]["policy"]["allow_create_memory"])


if __name__ == "__main__":
    unittest.main()
