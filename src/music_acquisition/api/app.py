# -*- coding: utf-8 -*-
"""REST /api/music/v1 — Dual-Mode HTTP surface (optional fastapi extra)."""

from __future__ import annotations

from typing import Any

from music_acquisition.domain.models import (
    AuthorizationRequest,
    MusicScopeConfig,
    SyncRequest,
)
from music_acquisition.engine.music_acquisition_engine import MusicAcquisitionEngine


def create_app(engine: MusicAcquisitionEngine):
    try:
        from fastapi import FastAPI, HTTPException
        from pydantic import BaseModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("install extras: pip install 'mnemosyne-music-acquisition[http]'") from exc

    app = FastAPI(title="Mnemosyne Music Acquisition", version="0.3.0")

    class BindBody(BaseModel):
        favorite_playlist_id: str
        subject_id: str | None = None

    class ScopeBody(BaseModel):
        owner_user_id: str
        scopes: list[str]
        purposes: list[str] = []

    class SyncBody(BaseModel):
        owner_user_id: str
        collectors: list[str] = []
        trigger: str = "manual"

    @app.get("/api/music/v1/health")
    async def health() -> dict[str, Any]:
        if not getattr(engine, "_initialized", False):
            st = await engine.initialize()
            return st.model_dump(mode="json")
        auth = await engine.get_authorization_status()
        from music_acquisition.version import __version__

        return {"ok": True, "auth_status": auth.status, "version": __version__}

    @app.post("/api/music/v1/auth/sessions")
    async def auth_start(body: AuthorizationRequest) -> dict[str, Any]:
        if not engine._initialized:
            await engine.initialize()
        ch = await engine.authorize(body)
        return ch.model_dump(mode="json")

    @app.get("/api/music/v1/auth/sessions/{session_id}")
    async def auth_status(session_id: str) -> dict[str, Any]:
        st = await engine.get_authorization_status(session_id)
        return st.model_dump(mode="json")

    @app.post("/api/music/v1/auth/sessions/{session_id}/config-bind")
    async def auth_bind(session_id: str, body: BindBody) -> dict[str, Any]:
        st = await engine.complete_config_bind(
            session_id,
            favorite_playlist_id=body.favorite_playlist_id,
            subject_id=body.subject_id,
        )
        return st.model_dump(mode="json")

    @app.post("/api/music/v1/auth/revoke")
    async def auth_revoke(subject_id: str) -> dict[str, Any]:
        await engine.revoke_authorization(subject_id)
        return {"ok": True}

    @app.put("/api/music/v1/scopes")
    async def scopes_put(body: ScopeBody) -> dict[str, Any]:
        await engine.configure_scopes(
            MusicScopeConfig(
                owner_user_id=body.owner_user_id,
                scopes=body.scopes,
                purposes=body.purposes,
            )
        )
        return {"ok": True, "scopes": body.scopes}

    @app.post("/api/music/v1/acquisition/sync")
    async def sync_now(body: SyncBody) -> dict[str, Any]:
        run = await engine.sync_now(
            SyncRequest(
                owner_user_id=body.owner_user_id,
                collectors=body.collectors,
                trigger=body.trigger,  # type: ignore[arg-type]
            )
        )
        return run.model_dump(mode="json")

    @app.get("/api/music/v1/sync/runs/{run_id}")
    async def sync_run(run_id: str) -> dict[str, Any]:
        return (await engine.get_sync_status(run_id)).model_dump(mode="json")

    @app.get("/api/music/v1/snapshots")
    async def snapshots(data_type: str, subject_id: str, local_date: str | None = None) -> dict[str, Any]:
        items = await engine.get_snapshots(
            data_type=data_type, subject_id=subject_id, local_date=local_date
        )
        return {"count": len(items), "items": [i.model_dump(mode="json") for i in items]}

    @app.post("/api/music/v1/recall")
    async def recall(subject_id: str, query: str = "") -> dict[str, Any]:
        return engine.recall(subject_id=subject_id, query=query)

    @app.post("/api/music/v1/admin/submit-outbox")
    async def submit_outbox(
        promote_graph: bool = False,
        preference_signals: bool = False,
        dry_run: bool = True,
        user_anchor: str = "",
    ) -> dict[str, Any]:
        return engine.submit_evidence_outbox(
            promote_graph=promote_graph,
            publish_preference_signals=preference_signals,
            dry_run_graph=dry_run,
            user_anchor_memory_id=user_anchor,
        )

    @app.post("/api/music/v1/domain/register")
    async def domain_register(pack_path: str = "") -> dict[str, Any]:
        return engine.register_music_domain(pack_path=pack_path)

    @app.post("/api/music/v1/graph/promote")
    async def graph_promote(dry_run: bool = True, user_anchor: str = "") -> dict[str, Any]:
        return engine.promote_music_graph(dry_run=dry_run, user_anchor_memory_id=user_anchor)

    @app.post("/api/music/v1/preference/signals")
    async def preference_signals(dry_run: bool = True) -> dict[str, Any]:
        return engine.publish_preference_signals(dry_run=dry_run)

    class ConfirmLikeBody(BaseModel):
        target_id: str
        title: str = ""
        target_type: str = "track"
        note: str = ""
        memory_id: str = ""
        evidence_refs: list[str] = []
        dry_run: bool = False

    @app.post("/api/music/v1/preference/confirm")
    async def preference_confirm(body: ConfirmLikeBody) -> dict[str, Any]:
        """Explicit user confirm → likes. Never call from silent sync."""
        return engine.confirm_like(
            target_id=body.target_id,
            title=body.title,
            target_type=body.target_type,
            note=body.note,
            memory_id=body.memory_id,
            evidence_refs=body.evidence_refs,
            dry_run=body.dry_run,
        )

    class RejectPrefBody(BaseModel):
        memory_id: str = ""
        target_id: str = ""
        note: str = ""
        dry_run: bool = False

    @app.post("/api/music/v1/preference/reject")
    async def preference_reject(body: RejectPrefBody) -> dict[str, Any]:
        return engine.reject_preference_signal(
            memory_id=body.memory_id,
            target_id=body.target_id,
            note=body.note,
            dry_run=body.dry_run,
        )

    class UnconfirmLikeBody(BaseModel):
        target_id: str = ""
        title: str = ""
        target_type: str = "track"
        memory_id: str = ""
        note: str = ""
        dry_run: bool = False

    @app.post("/api/music/v1/preference/unconfirm")
    async def preference_unconfirm(body: UnconfirmLikeBody) -> dict[str, Any]:
        """Explicit revoke of confirmed likes — does not unsaved NetEase tracks."""
        return engine.unconfirm_like(
            target_id=body.target_id,
            title=body.title,
            target_type=body.target_type,
            memory_id=body.memory_id,
            note=body.note,
            dry_run=body.dry_run,
        )

    @app.get("/api/music/v1/monitor/snapshot")
    async def monitor_snapshot() -> dict[str, Any]:
        return engine.monitor_snapshot()

    @app.get("/monitor/")
    @app.get("/monitor")
    async def monitor_page():
        from fastapi.responses import HTMLResponse
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "monitor" / "index.html"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="monitor_ui_missing")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.post("/api/music/v1/user-anchor/ensure")
    async def user_anchor_ensure(subject_key: str = "") -> dict[str, Any]:
        return engine.ensure_user_anchor(subject_key=subject_key)

    class BootstrapBody(BaseModel):
        owner_user_id: str = "owner"
        mode: str = "config_bind"
        favorite_playlist_id: str = ""
        subject_id: str = ""
        collectors: list[str] = []
        promote_graph: bool = True
        preference_signals: bool = True
        commit: bool = False
        wait_qr_sec: float = 0

    @app.post("/api/music/v1/bootstrap")
    async def bootstrap(body: BootstrapBody) -> dict[str, Any]:
        return await engine.bootstrap_pipeline(
            owner_user_id=body.owner_user_id,
            mode=body.mode,
            favorite_playlist_id=body.favorite_playlist_id,
            subject_id=body.subject_id,
            collectors=body.collectors or None,
            promote_graph=body.promote_graph,
            preference_signals=body.preference_signals,
            commit=body.commit,
            wait_qr_sec=body.wait_qr_sec,
        )

    @app.post("/api/music/v1/playback/song")
    async def play_song(track_id: str) -> dict[str, Any]:
        return engine.playback.play_song(track_id)

    @app.post("/api/music/v1/playback/next")
    async def play_next(delta: int = 1) -> dict[str, Any]:
        return engine.playback.play_next_in_scope(delta=delta)

    class RoundtripBody(BaseModel):
        track_id: str | None = None
        scope_key: str | None = None
        index_1based: int | None = None
        include_runtime: bool = False
        prefer_client: bool = True

    @app.post("/api/music/v1/playlist/roundtrip")
    async def playlist_roundtrip(body: RoundtripBody) -> dict[str, Any]:
        return engine.playback.roundtrip_lookup(
            track_id=body.track_id,
            scope_key=body.scope_key,
            index_1based=body.index_1based,
            include_runtime=body.include_runtime,
            prefer_client=body.prefer_client,
        )

    @app.get("/api/music/v1/playlist/scopes")
    async def playlist_scopes() -> dict[str, Any]:
        return engine.playlist_scopes.snapshot()

    @app.post("/api/music/v1/playlist/scopes/sync")
    async def playlist_scopes_sync() -> dict[str, Any]:
        return engine.sync_playlist_scopes_from_client()

    @app.post("/api/music/v1/playlist/scopes/hydrate")
    async def playlist_scopes_hydrate() -> dict[str, Any]:
        return engine.hydrate_playlist_scopes_to_client()

    @app.get("/api/music/v1/admin/health-detail")
    async def health_detail() -> dict[str, Any]:
        from music_acquisition.observability import engine_health_payload

        auth = await engine.get_authorization_status()
        payload = engine_health_payload(ok=True, auth_status=auth.status)
        payload["playlist_scopes"] = engine.playlist_scopes.snapshot()
        payload["data_root"] = engine._host.local_storage.root()
        return payload

    return app
