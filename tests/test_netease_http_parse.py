# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from music_acquisition.source.netease.http_client import extract_track_ids, parse_track
from music_acquisition.sync.retry import backoff_delay, should_retry
from music_acquisition.legacy.music_memory_bridge import profile_to_evidence_candidates


class NeteaseParseTests(unittest.TestCase):
    def test_parse_track(self) -> None:
        t = parse_track(
            {
                "id": 1446615816,
                "name": "测试曲",
                "ar": [{"id": 1, "name": "歌手"}],
                "al": {"id": 2, "name": "专辑"},
                "dt": 200000,
            }
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t["provider_track_id"], "1446615816")
        self.assertEqual(t["artists"][0]["name"], "歌手")

    def test_extract_track_ids_dict_form(self) -> None:
        ids = extract_track_ids({"trackIds": [{"id": 1}, {"id": 2}, 3, {"id": "x"}]})
        self.assertEqual(ids, [1, 2, 3])

    def test_retry_policy(self) -> None:
        self.assertFalse(should_retry("LOGIN_REQUIRED"))
        self.assertTrue(should_retry("NETWORK_TIMEOUT"))
        self.assertGreater(backoff_delay(2), backoff_delay(0))

    def test_legacy_profile_mapping(self) -> None:
        items = profile_to_evidence_candidates(
            {
                "tracks": {"100": {"song_id": 100, "last_at": "t"}},
                "taste_notes": ["我喜欢雨声"],
            }
        )
        self.assertTrue(any(i["evidence_type"] == "music.explicit_preference" for i in items))
        self.assertTrue(any(i["evidence_type"] == "music.played" for i in items))


if __name__ == "__main__":
    unittest.main()
