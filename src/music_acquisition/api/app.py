# -*- coding: utf-8 -*-
"""REST /api/music/v1 — Dual-Mode HTTP surface (optional fastapi extra)."""

from typing import Any
import os
import secrets
from music_acquisition.domain.models import MusicConsent

from music_acquisition.domain.models import (
    AuthorizationRequest,
    MusicScopeConfig,
    SyncRequest,
)
from music_acquisition.engine.music_acquisition_engine import MusicAcquisitionEngine


def create_app(engine: MusicAcquisitionEngine, *, access_token: str | None = None, agent_token: str | None = None):
    try:
        from fastapi import FastAPI, HTTPException, Request, Depends, Header
        from pydantic import BaseModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("install extras: pip install 'mnemosyne-music-acquisition[http]'") from exc

    from fastapi.responses import JSONResponse
    token = access_token or os.environ.get("MNEMOSYNE_MUSIC_API_TOKEN", "")
    business_token = agent_token or os.environ.get("MNEMOSYNE_MUSIC_AGENT_TOKEN", "")
    if token and business_token and secrets.compare_digest(token, business_token):
        raise ValueError("owner_and_agent_tokens_must_differ")
    async def authenticated(request: Request):
        if not request.url.path.startswith("/api/"):
            return
        supplied = request.headers.get("authorization", "")
        if supplied.startswith("Bearer "):
            value = supplied[7:]
            if token and secrets.compare_digest(value, token):
                return
            if business_token and secrets.compare_digest(value, business_token):
                allowed_get = {"health", "capabilities", "snapshots", "sync/status", "playlist/scopes"}
                suffix = request.url.path.removeprefix("/api/music/v1/")
                if (request.method == "GET" and suffix in allowed_get) or (request.method == "POST" and (suffix == "acquisition/sync" or suffix in {"playback/song", "playback/playlist", "playback/daily", "playback/next", "recall", "playlist/roundtrip"})):
                    return
                raise HTTPException(status_code=403, detail="owner_action_required")
        raise HTTPException(status_code=401, detail="authentication_required")
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def lifespan(app):
        if not engine._initialized:
            await engine.initialize()
        await engine.recover_startup(engine._owner_user_id)
        yield
        await engine.shutdown()
    app = FastAPI(title="Mnemosyne Music Acquisition", version="0.5.0", dependencies=[Depends(authenticated)], lifespan=lifespan)

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse(status_code=403, content={"detail": "permission_denied"})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=400, content={"detail": "invalid_request"})

    class BindBody(BaseModel):
        favorite_playlist_id: str
        subject_id: str | None = None
        state_nonce: str

    class ScopeBody(BaseModel):
        owner_user_id: str
        scopes: list[str]
        purposes: list[str] = []

    class SyncBody(BaseModel):
        owner_user_id: str
        collectors: list[str] = []
        trigger: str = "manual"
        playlist_ids: list[str] = []
        force_refresh: bool = False

    class RetentionBody(BaseModel):
        snapshot_days: int = 30
        outbox_days: int = 90
        audit_days: int = 180

    @app.put("/api/music/v1/admin/retention/{name}")
    async def retention_policy(name: str, body: RetentionBody):
        return engine.set_retention_policy(name, body.model_dump())

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
    async def auth_status(session_id: str, state_nonce: str = Header(alias="X-Music-Session-Nonce")) -> dict[str, Any]:
        st = await engine.get_authorization_status(session_id, state_nonce=state_nonce)
        return st.model_dump(mode="json")

    @app.post("/api/music/v1/auth/sessions/{session_id}/config-bind")
    async def auth_bind(session_id: str, body: BindBody) -> dict[str, Any]:
        st = await engine.complete_config_bind(
            session_id,
            favorite_playlist_id=body.favorite_playlist_id,
            subject_id=body.subject_id,
            state_nonce=body.state_nonce,
        )
        return st.model_dump(mode="json")

    @app.post("/api/music/v1/auth/sessions/{session_id}/cancel")
    async def auth_cancel(session_id: str, state_nonce: str = Header(alias="X-Music-Session-Nonce")):
        return engine._auth.cancel(session_id, state_nonce)

    @app.post("/api/music/v1/consent")
    async def consent(body: MusicConsent):
        return await engine.set_consent(body)

    @app.get("/api/music/v1/scopes")
    async def scopes_get():
        consent = engine._gate.consent(engine._owner_user_id)
        return {"scopes": engine._gate.get_scopes(engine._owner_user_id), "consent": consent.model_dump(mode="json") if consent else None}

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
                playlist_ids=body.playlist_ids,
                force_refresh=body.force_refresh,
            )
        )
        return run.model_dump(mode="json")

    @app.get("/api/music/v1/sync/runs/{run_id}")
    async def sync_run(run_id: str) -> dict[str, Any]:
        return (await engine.get_sync_status(run_id)).model_dump(mode="json")

    @app.get("/api/music/v1/sync/status")
    async def sync_status(run_id: str | None = None):
        return (await engine.get_sync_status(run_id)).model_dump(mode="json")

    @app.get("/api/music/v1/admin/context")
    async def context():
        return {"owner_user_id": engine._owner_user_id, "node_id": engine._node_id,
            "scopes": engine._gate.get_scopes(engine._owner_user_id)}

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
        import asyncio
        payload = await asyncio.to_thread(engine.monitor_snapshot)
        payload["auth"] = (await engine.get_authorization_status()).model_dump(mode="json")
        return payload

    @app.get("/monitor/")
    @app.get("/monitor")
    async def monitor_page():
        from fastapi.responses import HTMLResponse
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "resources" / "monitor.html"
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
        engine._require_purpose("music.data.read")
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
        payload = {"engine": engine._initialized, "authorization": auth.status, "metrics": engine.metrics()}
        payload["playlist_scopes"] = engine.playlist_scopes.snapshot()
        payload["data_root"] = engine._host.local_storage.root()
        return payload

    @app.get("/api/music/v1/sync/runs")
    async def sync_history():
        return engine.sync_history()

    @app.post("/api/music/v1/sync/recover")
    async def recover():
        return {"runs": [r.model_dump(mode="json") for r in await engine.recover_startup(engine._owner_user_id)]}

    @app.post("/api/music/v1/admin/cleanup")
    async def cleanup(retention_days: int = 30, confirm: bool = False):
        return engine.cleanup(retention_days=retention_days, confirm=confirm)

    @app.get("/api/music/v1/capabilities")
    async def capabilities():
        return (await engine.source.get_capabilities()).model_dump(mode="json")

    @app.post("/api/music/v1/admin/submit-operations")
    async def submit_operations():
        return engine.submit_operations()

    @app.post("/api/music/v1/admin/schedule")
    async def schedule(enabled: bool, interval_sec: float = 86400):
        return engine.enable_auto_sync(interval_sec=interval_sec) if enabled else engine.disable_auto_sync()

    @app.get("/api/music/v1/admin/export")
    async def export():
        return engine.export_data()

    @app.post("/api/music/v1/admin/delete")
    async def delete(target: str, confirm: bool = False, object_id: str = ""):
        return engine.delete_data(target=target, confirm=confirm, object_id=object_id)

    @app.get("/api/music/v1/diff")
    async def diffs():
        engine._require_purpose("music.data.read")
        from pathlib import Path
        import json
        return {"items": [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(engine._host.local_storage.root()) / "diffs").glob("*.json"))[-100:]]}

    @app.post("/api/music/v1/playback/playlist")
    async def playback_playlist(playlist_id: str, track_id: str | None = None, index: int | None = None):
        return engine.playback.play_playlist(playlist_id, track_id=track_id, index=index)

    @app.post("/api/music/v1/playback/daily")
    async def playback_daily(date: str, track_id: str | None = None, index: int | None = None):
        return engine.playback.play_daily(date, track_id=track_id, index=index)

    return app
