"""Acceptance for the text requirements, using synthetic data and temporary roots."""
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from music_acquisition.api.app import create_app
from music_acquisition.domain.models import AuthorizationRequest, MusicConsent, MusicScopeConfig, SyncRequest
from music_acquisition.engine import MusicAcquisitionEngine
from music_acquisition.hosts.in_memory import InMemoryHostRuntime
from music_acquisition.source.mock_adapter import MockNeteaseAdapter
from music_acquisition.snapshot.manager import SnapshotManager
from music_acquisition.snapshot.hashing import snapshot_hash
from music_acquisition.sync.recovery import list_pending_run_ids


class CookieSource(MockNeteaseAdapter):
    def export_cookie(self):
        return "SYNTHETIC_ONLY"
    def import_cookie(self, value, subject_id=None):
        self._authorized = bool(value)


def run(coro):
    return asyncio.run(coro)


async def consent(engine, purposes=None, expires_at=None):
    await engine.set_consent(MusicConsent(consent_id="fixture-consent", owner_user_id=engine._owner_user_id,
        node_id=engine._node_id, granted_at="2026-01-01T00:00:00Z", expires_at=expires_at,
        scopes=engine._gate.get_scopes(engine._owner_user_id), purposes=purposes if purposes is not None else
        ["music.data.read", "music.data.cache", "music.data.submit_evidence", "music.data.create_memory", "music.data.recall", "music.data.delete"]))


@pytest.fixture
def env(tmp_path):
    host = InMemoryHostRuntime(tmp_path)
    source = CookieSource(liked_count=2)
    engine = MusicAcquisitionEngine(host, source=source, owner_user_id="u1")
    async def setup():
        await engine.initialize()
        ch = await engine.authorize(AuthorizationRequest(owner_user_id="u1"))
        await engine.get_authorization_status(ch.session_id, state_nonce=ch.state_nonce)
        await engine.configure_scopes(MusicScopeConfig(owner_user_id="u1", scopes=["music.library.liked_songs", "music.recommend.daily_songs"]))
        return ch
    ch = run(setup())
    yield host, source, engine, ch
    run(engine.shutdown())


def test_api_rejects_anonymous_and_accepts_json_body(env):
    host, _, engine, _ = env
    client = TestClient(create_app(engine, access_token="fixture-api"))
    assert client.get("/api/music/v1/snapshots?data_type=liked_songs&subject_id=mock_uid_1").status_code == 401
    headers = {"Authorization": "Bearer fixture-api"}
    response = client.put("/api/music/v1/scopes", headers=headers, json={"owner_user_id": "u1", "scopes": ["music.library.liked_songs"]})
    assert response.status_code == 200, response.text
    assert client.put("/api/music/v1/scopes", headers=headers, json={"owner_user_id": "other", "scopes": []}).status_code == 403


def test_no_consent_and_expired_consent_cannot_collect(env):
    host, _, engine, _ = env
    request = SyncRequest(owner_user_id="u1", collectors=["liked_songs"])
    assert run(engine.sync_now(request)).error_code == "CONSENT_REQUIRED"
    run(consent(engine, expires_at="2000-01-01T00:00:00Z"))
    assert run(engine.sync_now(request)).error_code == "CONSENT_REQUIRED"
    assert not (Path(host.local_storage.root()) / "snapshots").exists()


def test_scope_and_uses_are_separate(env):
    host, _, engine, _ = env
    run(consent(engine, purposes=["music.data.read", "music.data.cache"]))
    result = run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["liked_songs"])))
    assert result.status == "success" and result.consent_id == "fixture-consent"
    assert not engine._outbox.list_pending()
    with pytest.raises(PermissionError):
        engine.submit_evidence_outbox()
    with pytest.raises(PermissionError):
        engine.confirm_like(target_id="1")


def test_owner_is_not_a_request_parameter(env):
    _, _, engine, _ = env
    with pytest.raises(PermissionError):
        run(engine.sync_now(SyncRequest(owner_user_id="other", collectors=["liked_songs"])))
    with pytest.raises(PermissionError):
        run(engine.authorize(AuthorizationRequest(owner_user_id="other")))


def test_nonce_expiry_cancel_and_unknown_sessions(env):
    _, _, engine, ch = env
    with pytest.raises(PermissionError):
        run(engine.get_authorization_status(ch.session_id, state_nonce="bad"))
    assert run(engine.get_authorization_status("missing")).status == "failed"
    engine._auth._sessions[ch.session_id].expires_at = "2000-01-01T00:00:00Z"
    assert run(engine.get_authorization_status(ch.session_id)).status == "expired"
    ch2 = run(engine.authorize(AuthorizationRequest(owner_user_id="u1")))
    engine._auth.cancel(ch2.session_id, ch2.state_nonce)
    assert run(engine.get_authorization_status(ch2.session_id)).status == "cancelled"


def test_revoke_survives_restart_and_deletes_credential(env):
    host, _, engine, _ = env
    run(consent(engine))
    run(engine.revoke_authorization("mock_uid_1"))
    assert not run(host.credential_store.exists("cred:u1:netease"))
    other = MusicAcquisitionEngine(host, source=CookieSource(), owner_user_id="u1")
    run(other.initialize())
    assert run(other.get_authorization_status()).status == "revoked"
    assert other._gate.get_scopes("u1") == engine._gate.get_scopes("u1")
    assert not other._gate.consent_active("u1")


def test_count_mismatch_and_duplicates_never_overwrite(env):
    host, *_ = env
    manager = SnapshotManager(host)
    old = manager.commit(owner_user_id="u1", data_type="liked_songs", subject_id="s", records=[{"provider_track_id": "1"}], expected_count=1, complete=True)
    assert manager.commit(owner_user_id="u1", data_type="liked_songs", subject_id="s", records=[{"provider_track_id": "2"}], expected_count=100, complete=True) is None
    assert manager.commit(owner_user_id="u1", data_type="liked_songs", subject_id="s", records=[{"provider_track_id": "2"}, {"provider_track_id": "2"}], expected_count=2, complete=True) is None
    assert manager.load("liked_songs", "s").snapshot_id == old.snapshot_id


def test_partial_is_partial_and_stale_snapshot_not_resubmitted(env):
    host, source, engine, _ = env
    run(consent(engine))
    original = source.get_liked_songs
    async def partial():
        r = await original()
        return r.model_copy(update={"complete": False, "status": "partial", "expected_count": 50})
    source.get_liked_songs = partial
    engine._sync.retry_base = 0
    result = run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["liked_songs"])))
    assert result.status == "partial"
    assert not engine._outbox.list_pending()


def test_transient_retry_persistence_and_recovery(env):
    host, source, engine, _ = env
    run(consent(engine))
    engine._sync.retry_base = 0
    calls = []
    original = source.get_liked_songs
    async def unavailable():
        calls.append(1)
        raise TimeoutError("synthetic")
    source.get_liked_songs = unavailable
    failed = run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["liked_songs"])))
    assert len(calls) == 3 and failed.run_id in list_pending_run_ids(host)
    other = MusicAcquisitionEngine(host, source=CookieSource(liked_count=2), owner_user_id="u1")
    run(other.initialize())
    assert run(other.get_sync_status(failed.run_id)).status == "failed"
    recovered = run(other.recover_startup("u1"))
    assert recovered and recovered[0].status == "success"
    assert failed.run_id not in list_pending_run_ids(host)


def test_user_date_and_daily_cache(env):
    host, source, engine, _ = env
    class Clock:
        def now_iso(self): return "2026-11-01T16:30:00Z"
        def timezone(self): return "Asia/Shanghai"
    host.clock = Clock()
    run(consent(engine))
    original, calls = source.get_daily_songs, []
    async def collect():
        calls.append(1)
        return await original()
    source.get_daily_songs = collect
    request = SyncRequest(owner_user_id="u1", collectors=["daily_songs"])
    assert run(engine.sync_now(request)).status == "success"
    assert run(engine.sync_now(request)).status == "success"
    assert len(calls) == 1
    assert host.local_storage.read_json("snapshots/daily_songs/mock_uid_1/2026-11-02.json")


def test_hash_diff_metadata_order_and_idempotent_outbox(env):
    host, _, engine, _ = env
    run(consent(engine))
    a = [{"provider_track_id": "1", "title": "A", "metadata": {"fetched_at": "t1"}}, {"provider_track_id": "2"}]
    b = [{"provider_track_id": "1", "title": "A", "metadata": {"fetched_at": "t2"}}, {"provider_track_id": "2"}]
    assert snapshot_hash(a) == snapshot_hash(b)
    manager = SnapshotManager(host)
    old = manager.commit(owner_user_id="u1", data_type="liked_songs", subject_id="s", records=a, complete=True)
    new = manager.commit(owner_user_id="u1", data_type="liked_songs", subject_id="s", records=[b[1], {**b[0], "title": "B"}], complete=True)
    assert {c.change_type for c in manager.diff_against_previous(previous=old, current=new).changes} == {"metadata_changed", "order_changed"}
    request = SyncRequest(owner_user_id="u1", collectors=["liked_songs"])
    run(engine.sync_now(request)); run(engine.sync_now(request))
    assert len(engine._outbox.list_pending()) == 1


def test_offline_confirmation_is_pending_not_a_second_authority(env):
    host, _, engine, _ = env
    run(consent(engine))
    result = engine.confirm_like(target_id="1")
    assert not result["ok"] and result["status"] == "pending" and not result["submitted"]
    assert not list((Path(host.local_storage.root()) / "memory_bank_mirror").rglob("*.json"))


def test_unsupported_collector_is_not_generic_failure(env):
    _, _, engine, _ = env
    result = run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["unknown-feature"])))
    assert result.status == "unsupported"


def test_delete_preview_and_retention_never_touch_node_or_credentials(env):
    host, _, engine, _ = env
    run(consent(engine))
    run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["liked_songs"])))
    preview = engine.delete_data(target="snapshots")
    assert preview["preview"] and preview["count"] > 0
    engine.delete_data(target="snapshots", confirm=True)
    assert run(host.credential_store.exists("cred:u1:netease"))
    assert not list((Path(host.local_storage.root()) / "snapshots").rglob("*.json"))


def test_paths_cannot_leave_the_data_root(env):
    host, *_ = env
    with pytest.raises(ValueError):
        host.local_storage.write_json("../../outside.json", {})
