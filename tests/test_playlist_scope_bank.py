# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from music_acquisition.bank_paths import MEMORY_BANK_NAME, music_acquisition_root
from music_acquisition.playlist_scope_store import SCOPE_DAILY, SCOPE_QUEUE, PlaylistScopeStore


def test_bank_paths_under_tmp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MNEMOSYNE_MEMORY_BANK", str(tmp_path / MEMORY_BANK_NAME))
    monkeypatch.delenv("MNEMOSYNE_MUSIC_DATA", raising=False)
    root = music_acquisition_root(create=True)
    assert root.name == "music-acquisition"
    assert MEMORY_BANK_NAME in str(root)
    assert (root / "playlist_scopes").is_dir()


def test_scope_store_roundtrip_persists(tmp_path: Path) -> None:
    store = PlaylistScopeStore(tmp_path / "playlist_scopes")
    daily = [{"track_id": str(i), "title": f"t{i}"} for i in range(1, 28)]
    daily.append(
        {"track_id": "1470902634", "title": "At Least I Had Fun", "artists": "Martin Jensen/RANI"}
    )
    store.upsert(
        SCOPE_DAILY,
        name="每日推荐",
        tracks=daily,
        kind="source",
        scene="dailyRecommend",
        activate=True,
        viewed=True,
        mirror_to_client=False,
    )
    queue = [{"track_id": str(9000 + i), "title": f"q{i}"} for i in range(1, 28)]
    queue.append({"track_id": "999", "title": "雪乡（Snow Town）"})
    store.upsert(
        SCOPE_QUEUE,
        name="当前播放队列",
        tracks=queue,
        kind="runtime",
        mirror_to_client=False,
    )

    d28 = store.lookup_index(SCOPE_DAILY, 28)
    q28 = store.lookup_index(SCOPE_QUEUE, 28)
    assert d28 and d28["title"] == "At Least I Had Fun"
    assert q28 and q28["title"] == "雪乡（Snow Town）"
    assert d28["track_id"] != q28["track_id"]

    back = store.roundtrip(track_id="1470902634")
    assert back["ok"] and back["resolved"]["index_1based"] == 28
    assert store.resolve_active_source() == SCOPE_DAILY
