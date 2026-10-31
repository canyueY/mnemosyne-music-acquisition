import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from test_audit_compliance import env, run, consent
from music_acquisition.api.app import create_app
from music_acquisition.domain.models import MusicScopeConfig, SyncRequest
from music_acquisition.governance.scopes import ALL_SCOPES
from music_acquisition.source.netease.adapter import NeteaseSourceAdapter
from music_acquisition.source.netease import http_client as api
from music_acquisition.source.http_error import SourceHttpError
from music_acquisition.integrations.mcp import MusicToolClient, MusicMcpServer
from music_acquisition.util.file_lock import FileLock


def test_no_fixed_500_or_5000_track_cap(monkeypatch):
    tracks = [{"id": i, "name": str(i)} for i in range(1, 6202)]
    def request(url, params, **kwargs):
        if url == api.PLAYLIST_DETAIL:
            return {"playlist": {"trackCount": len(tracks), "tracks": tracks[:500], "trackIds": [{"id": t["id"]} for t in tracks]}}
        ids = [r["id"] for r in json.loads(params["c"])]
        return {"songs": [tracks[i - 1] for i in ids]}
    monkeypatch.setattr(api, "_get_json", request)
    rows, expected, complete = api.fetch_playlist_tracks(123, cookie="SYNTHETIC")
    assert complete and expected == len(rows) == 6201
    assert [r["provider_track_id"] for r in rows] == [str(i) for i in range(1, 6202)]


def test_page_checkpoint_reuses_only_completed_recent_pages(env, monkeypatch):
    host, *_ = env
    def request(url, params, **kwargs):
        return {"playlist": {"trackCount": 3, "tracks": [], "trackIds": [{"id": i} for i in range(1, 4)]}}
    calls = []
    def songs(ids, **kwargs):
        calls.append(1)
        return [{"provider_track_id": str(i)} for i in ids]
    monkeypatch.setattr(api, "_get_json", request)
    monkeypatch.setattr(api, "fetch_songs_by_ids", songs)
    assert api.fetch_playlist_tracks(1, page_cache=host.local_storage)[2]
    assert api.fetch_playlist_tracks(1, page_cache=host.local_storage)[2]
    assert len(calls) == 1
    for path in (Path(host.local_storage.root()) / "pages").glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8")); value["cached_epoch"] = 0
        path.write_text(json.dumps(value), encoding="utf-8")
    assert api.fetch_playlist_tracks(1, page_cache=host.local_storage)[2]
    assert len(calls) == 2


def test_catalog_paginates_and_deduplicates(monkeypatch):
    source = NeteaseSourceAdapter(cookie="SYNTHETIC", subject_id="42")
    offsets = []
    def request(url, *, params, **kwargs):
        offset = int(params["offset"]); offsets.append(offset)
        rows = [{"id": i, "name": str(i)} for i in range(offset, min(offset + 100, 250))]
        return {"code": 200, "playlist": rows, "more": offset < 200}, ""
    monkeypatch.setattr("music_acquisition.source.netease.qr_auth._request", request)
    result = run(source.get_playlists())
    # id zero is invalid; other 249 items preserve their source identities.
    assert result.complete and result.actual_count == 249 and offsets == [0, 100, 200]


def test_daily_login_error_never_becomes_empty_success(monkeypatch):
    source = NeteaseSourceAdapter(cookie="SYNTHETIC", subject_id="42")
    monkeypatch.setattr("music_acquisition.source.netease.qr_auth._request", lambda *a, **kw: ({"code": 301}, ""))
    with pytest.raises(PermissionError):
        run(source.get_daily_songs())


def test_public_binding_is_not_account_authorization():
    source = NeteaseSourceAdapter()
    challenge = run(source.begin_config_bind())
    assert source.complete_config_bind(challenge.session_id, favorite_playlist_id="123").status == "public_only"
    assert run(source.check_authorization()).status == "public_only"


def test_nonempty_cookie_is_not_proof_of_login(monkeypatch):
    source = NeteaseSourceAdapter(cookie="SYNTHETIC", subject_id="42")
    monkeypatch.setattr("music_acquisition.source.netease.qr_auth.fetch_account", lambda *a: {"code": 301})
    assert run(source.check_authorization()).status == "expired"
    assert source.export_cookie() == ""


def test_favorite_artist_pagination_beyond_500(monkeypatch):
    source = NeteaseSourceAdapter(cookie="SYNTHETIC", subject_id="42")
    def page(*, offset, **kw):
        return {"code": 200, "records": [{"provider_artist_id": str(i)} for i in range(offset, offset + 100)], "has_more": offset < 600, "count": 700}
    monkeypatch.setattr("music_acquisition.source.netease.weapi.fetch_sub_artists", page)
    result = run(source.get_favorite_artists())
    assert result.complete and result.expected_count == result.actual_count == 700


def test_playlist_tracks_are_dispatched_with_parent_identity(env):
    host, source, engine, _ = env
    run(engine.configure_scopes(MusicScopeConfig(owner_user_id="u1", scopes=ALL_SCOPES)))
    run(consent(engine))
    result = run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["playlist_tracks", "daily_playlists"], playlist_ids=["1", "2"])))
    assert result.collector_results["playlist_tracks"]["status"] == "success"
    snap = engine._snapshots.load("playlist_tracks", "mock_uid_1")
    assert snap.complete and {r["parent_playlist_id"] for r in snap.records} == {"1", "2"}


def test_os_lock_excludes_another_process(tmp_path):
    path = tmp_path / "same-account.lock"
    source = "from pathlib import Path; from music_acquisition.util.file_lock import FileLock; import sys\ntry:\n with FileLock(Path(sys.argv[1]),timeout_sec=0): print('acquired')\nexcept TimeoutError: print('busy')"
    child_env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    with FileLock(path):
        result = subprocess.run([sys.executable, "-c", source, str(path)], capture_output=True, text=True, env=child_env)
        assert result.returncode == 0 and result.stdout.strip() == "busy", result.stderr
    result = subprocess.run([sys.executable, "-c", source, str(path)], capture_output=True, text=True, env=child_env)
    assert result.returncode == 0 and result.stdout.strip() == "acquired"


def test_scope_withdrawal_during_fetch_prevents_commit(env):
    host, source, engine, _ = env
    run(consent(engine))
    original = source.get_liked_songs
    async def collect():
        result = await original()
        await engine.configure_scopes(MusicScopeConfig(owner_user_id="u1", scopes=[]))
        return result
    source.get_liked_songs = collect
    result = run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["liked_songs"])))
    assert result.status == "auth_required"
    assert not engine._snapshots.load("liked_songs", "mock_uid_1")


def test_retry_after_survives_restart_without_early_requests(env):
    host, source, engine, _ = env
    run(consent(engine))
    calls = []
    async def throttled():
        calls.append(1)
        raise SourceHttpError(429, "300")
    source.get_liked_songs = throttled
    result = run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["liked_songs"])))
    assert result.status == "failed" and result.next_retry_at
    assert run(engine.recover_startup("u1")) == []
    second = run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["liked_songs"], force_refresh=True)))
    assert second.next_retry_at == result.next_retry_at and len(calls) == 1


@pytest.fixture
def bridge(monkeypatch):
    state = {"id": "1", "playing": True, "launch_ok": True, "launched": []}
    package, music, cdp, utils = [ModuleType(n) for n in ("Murasame", "Murasame.netease_music", "Murasame.netease_cdp", "Murasame.utils")]
    def launch(value):
        state["launched"].append(value)
        return state["launch_ok"]
    music.is_enabled = lambda: True
    music.launch_cloudmusic_song = launch
    music._try_cdp_ready = lambda config: (True, 9999)
    cdp.cdp_is_playing = lambda **kw: state["playing"]
    cdp.cdp_current_track_id = lambda **kw: state["id"]
    cdp.cdp_get_playing_track_info = lambda **kw: {}
    utils.get_config = lambda: {}
    package.netease_music, package.netease_cdp = music, cdp
    for module in (package, music, cdp, utils): monkeypatch.setitem(sys.modules, module.__name__, module)
    return state


def test_launch_failure_and_wrong_id_never_report_success(bridge):
    from music_acquisition.playback.service import MusicPlaybackService
    playback = MusicPlaybackService(verify_timeout_sec=.02, verify_poll_sec=.001)
    bridge["launch_ok"] = False
    assert playback.play_song("1")["error"] == "PLAY_FAILED"
    bridge.update(launch_ok=True, id="91234")
    assert not playback.play_song("1234")["ok"]
    bridge["id"] = "1234"
    assert playback.play_song("1234")["ok"]
    assert not playback.play_song("1234", verify=False)["ok"]
    assert not playback.play_playlist("9", track_id="1", index=1)["ok"]


def test_playlist_selection_and_next_use_complete_scope(env, bridge):
    host, _, engine, _ = env
    run(engine.configure_scopes(MusicScopeConfig(owner_user_id="u1", scopes=ALL_SCOPES)))
    run(consent(engine))
    engine._snapshots.commit(owner_user_id="u1", data_type="playlist_tracks", subject_id="mock_uid_1", complete=True,
        records=[{"provider_track_id": "1", "parent_playlist_id": "9"}, {"provider_track_id": "2", "parent_playlist_id": "9"}])
    playback = engine.playback
    playback._verify_timeout_sec, playback._verify_poll_sec = .02, .001
    assert playback.play_playlist("9", index=1)["ok"]
    assert playback.play_playlist("9", track_id="999")["error"] == "TRACK_NOT_IN_PLAYLIST"
    assert playback.play_playlist("9", index=3)["error"] == "INDEX_OUT_OF_RANGE"
    bridge["id"] = "2"
    result = playback.play_next_in_scope()
    # Current client track is 2, expected wrapped next target 1; client remains on 2.
    assert bridge["launched"][-1] == 1 and not result["ok"]
    assert playback.play_daily("2000-01-01", index=1)["error"] == "SNAPSHOT_EXPIRED"


def test_agent_token_cannot_grant_consent_or_confirm_preferences(env):
    _, _, engine, _ = env
    client = TestClient(create_app(engine, access_token="owner-secret", agent_token="agent-secret"))
    headers = {"Authorization": "Bearer agent-secret"}
    assert client.get("/api/music/v1/capabilities", headers=headers).status_code == 200
    assert client.put("/api/music/v1/scopes", headers=headers, json={"owner_user_id": "u1", "scopes": ALL_SCOPES}).status_code == 403
    assert client.post("/api/music/v1/auth/sessions", headers=headers, json={"owner_user_id": "u1"}).status_code == 403
    assert client.post("/api/music/v1/admin/delete?target=snapshots&confirm=true", headers=headers).status_code == 403


def test_mcp_protocol_and_tool_arguments_exclude_administration():
    client = MusicToolClient(base_url="http://unused", token="SYNTHETIC", owner_user_id="u1")
    server = MusicMcpServer(client)
    stream, output = io.StringIO('\n'.join([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})]) + '\n'), io.StringIO()
    server.serve(stream, output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(rows) == 2 and rows[0]["result"]["protocolVersion"] == "2025-06-18"
    assert len(rows[1]["result"]["tools"]) == 10
    with pytest.raises(ValueError): client.call("music_auth_cookie", {"cookie": "secret"})
    with pytest.raises(ValueError): client.call("music_sync", {"owner_user_id": "other"})


def test_retention_preview_then_physical_delete(env):
    host, _, engine, _ = env
    run(consent(engine))
    engine.set_retention_policy("short", {"snapshot_days": 1, "outbox_days": 1, "audit_days": 1})
    host.local_storage.write_json("snapshots/old.json", {"fetched_at": "2000-01-01T00:00:00Z"})
    assert engine.cleanup()["count"] == 1
    assert host.local_storage.read_json("snapshots/old.json")
    assert engine.cleanup(confirm=True)["count"] == 1
    assert host.local_storage.read_json("snapshots/old.json") is None


def test_pending_latest_user_intent_supersedes_earlier_confirmation(env):
    host, _, engine, _ = env
    run(consent(engine))
    first = engine.confirm_like(target_id="1")
    second = engine.unconfirm_like(target_id="1")
    old = host.local_storage.read_json(f"memory/operations/{first['operation_id']}.json")
    new = host.local_storage.read_json(f"memory/operations/{second['operation_id']}.json")
    assert old["status"] == "superseded" and new["status"] == "pending" and new["sequence"] > old["sequence"]
    assert engine.confirm_like(target_id="2", dry_run=True)["dry_run"]
