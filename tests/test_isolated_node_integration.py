"""Exercise the sibling Node's actual REST app against an isolated SQLite bank."""
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from test_audit_compliance import run, consent
from music_acquisition.domain.models import AuthorizationRequest, MusicScopeConfig, SyncRequest
from music_acquisition.engine import MusicAcquisitionEngine
from music_acquisition.hosts.in_memory import InMemoryHostRuntime
from music_acquisition.source.mock_adapter import MockNeteaseAdapter
from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient


def test_actual_node_submit_confirm_reconfirm_and_separate_deletion(tmp_path, monkeypatch):
    core = Path(__file__).resolve().parents[2] / "dsh-mnemosyne/service/src"
    if not core.is_dir(): pytest.skip("sibling Mnemosyne source unavailable")
    pytest.importorskip("pydantic_settings")
    monkeypatch.syspath_prepend(str(core))
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path / "node"))
    monkeypatch.setenv("DSH_MEMORY_EMOTION", "0")
    monkeypatch.setenv("DSH_MEMORY_SPATIOTEMPORAL", "0")
    from cognitive_memory.bootstrap import build_context
    context = build_context(data_dir=str(tmp_path / "node"), token="synthetic-node-token")
    try:
        with TestClient(context.app) as node:
            def request(self, method, path, body=None):
                assert self.base_url == "http://isolated-node"
                response = node.request(method, path, json=body, headers={"X-Memory-Token": "synthetic-node-token"})
                if response.status_code >= 400:
                    return {"ok": False, "error": f"http_{response.status_code}", "detail": response.json()}
                return response.json()
            monkeypatch.setattr(MnemosyneHttpClient, "_request", request)
            engine = MusicAcquisitionEngine(InMemoryHostRuntime(tmp_path / "component"), owner_user_id="u1",
                source=MockNeteaseAdapter(liked_count=2), mnemosyne_base_url="http://isolated-node", mnemosyne_token="synthetic-node-token")
            run(engine.initialize())
            challenge = run(engine.authorize(AuthorizationRequest(owner_user_id="u1")))
            run(engine.get_authorization_status(challenge.session_id))
            run(engine.configure_scopes(MusicScopeConfig(owner_user_id="u1", scopes=["music.library.liked_songs"])))
            run(consent(engine))
            domain = engine.register_music_domain()
            assert domain.get("ok"), domain
            assert run(engine.sync_now(SyncRequest(owner_user_id="u1", collectors=["liked_songs"]))).status == "success"
            submitted = engine.submit_evidence_outbox()
            assert submitted["ok"], submitted
            assert not engine._outbox.list_pending()
            confirmed = engine.confirm_like(target_id="123", title="synthetic track")
            assert confirmed.get("ok"), confirmed
            mid = confirmed["memory_id"]
            undone = engine.unconfirm_like(target_id="123", memory_id=mid)
            assert undone.get("ok"), undone
            reconfirmed = engine.confirm_like(target_id="123", title="synthetic track")
            assert reconfirmed.get("ok"), reconfirmed
            memory = engine._mnemosyne._request("GET", "/api/v1/memories/" + mid)
            assert memory["status"] == "active" and memory["metadata"]["status"] == "confirmed"
            relations = engine._bank.list_relations(mid)
            assert len([r for r in relations["items"] if r["relation"] == "likes"]) == 1
            assert engine.delete_data(target="node_memory", object_id=mid)["preview"]
            deleted = engine.delete_data(target="node_memory", object_id=mid, confirm=True)
            assert deleted.get("ok") is not False, deleted
            assert engine._snapshots.load("liked_songs", "mock_uid_1")
            run(engine.shutdown())
    finally:
        context.db.close()


