# -*- coding: utf-8 -*-
"""Music Acquisition Engine — host-agnostic core API (Phases 1–6 wired)."""

from __future__ import annotations

from music_acquisition.auth.manager import AuthManager
from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.contracts.source_adapter import MusicSourceAdapter
from music_acquisition.domain.models import (
    AuthorizationChallenge,
    AuthorizationRequest,
    AuthorizationStatus,
    EngineStatus,
    MusicDiff,
    MusicScopeConfig,
    MusicSnapshot,
    SyncRequest,
    SyncRun,
    SyncStatus,
    MusicConsent,
)
from music_acquisition.governance.scopes import PermissionGate
from music_acquisition.memory.adapter import MusicMemoryAdapter
from music_acquisition.memory.bank_backend import MemoryBankBackend, open_memory_bank
from music_acquisition.memory.graph_builder import MusicGraphBuilder
from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient
from music_acquisition.memory.outbox import EvidenceOutbox
from music_acquisition.memory.claims import ClaimBridge
from music_acquisition.memory.preference_confirm import (
    confirm_preference,
    reject_preference,
    unconfirm_preference,
)
from music_acquisition.memory.preference_signals import PreferenceSignalPublisher
from music_acquisition.memory.recall import MusicRecallService
from music_acquisition.memory.revoke import apply_revokes, diff_to_revoke_evidence
from music_acquisition.memory.submit import EvidenceSubmitter
from music_acquisition.playback.service import MusicPlaybackService
from music_acquisition.playlist_scope_store import PlaylistScopeStore, get_store
from music_acquisition.snapshot.manager import SnapshotManager
from music_acquisition.snapshot.ttl import mark_expired_if_needed
from music_acquisition.source.mock_adapter import MockNeteaseAdapter
from music_acquisition.source.netease.adapter import NeteaseSourceAdapter
from music_acquisition.sync.coordinator import SyncCoordinator
from music_acquisition.sync.recovery import startup_recovery_requests, list_pending_run_ids
from music_acquisition.version import __version__
from music_acquisition.util.runtime import audit, local_date as current_local_date


class MusicAcquisitionEngine:
    def __init__(
        self,
        host: HostRuntime,
        *,
        source: MusicSourceAdapter | None = None,
        node_id: str = "local-node",
        mnemosyne_base_url: str = "",
        mnemosyne_token: str = "",
        use_real_netease: bool = True,
        favorite_playlist_id: str = "",
        memory_bank_kind: str = "mnemosyne",
        owner_user_id: str = "owner",
    ) -> None:
        self._host = host
        self._node_id = node_id
        self._owner_user_id = owner_user_id
        if memory_bank_kind not in {"mnemosyne", "auto"}:
            raise ValueError("Mnemosyne_is_the_only_long_term_authority")
        if source is not None:
            self._source = source
        elif use_real_netease or favorite_playlist_id:
            self._source = NeteaseSourceAdapter(favorite_playlist_id=favorite_playlist_id or None)
        else:
            self._source = MockNeteaseAdapter()
        checkpoint = getattr(self._source, "set_checkpoint_store", None)
        if callable(checkpoint):
            checkpoint(host.local_storage)
        self._gate = PermissionGate()
        self._snapshots = SnapshotManager(host)
        self._auth = AuthManager(host, self._source, owner_user_id=owner_user_id)
        self._sync = SyncCoordinator(
            host, self._source, self._snapshots, self._gate, node_id=node_id, owner_user_id=owner_user_id
        )
        self._memory = MusicMemoryAdapter(host)
        self._outbox = EvidenceOutbox(host)
        self._mnemosyne = (
            MnemosyneHttpClient(mnemosyne_base_url, mnemosyne_token)
            if mnemosyne_base_url
            else None
        )
        from pathlib import Path

        local_bank = Path(host.local_storage.root()) / "memory_bank_mirror"
        self._bank: MemoryBankBackend | None = None
        if mnemosyne_base_url:
            self._bank = open_memory_bank(
                "mnemosyne",
                mnemosyne_base_url=mnemosyne_base_url,
                mnemosyne_token=mnemosyne_token,
                local_root=local_bank,
                audit_root=local_bank / "audit",
                client=self._mnemosyne,
            )
        self._submitter = EvidenceSubmitter(host, self._mnemosyne, self._outbox)
        self._recall = MusicRecallService(host, mnemosyne=self._mnemosyne)
        self._graph = MusicGraphBuilder(self._mnemosyne)
        self._prefs = PreferenceSignalPublisher(self._mnemosyne)
        scopes_root = Path(host.local_storage.root()) / "playlist_scopes"
        self._scope_store = get_store(scopes_root)
        self._playback = MusicPlaybackService(scope_store=self._scope_store, authorize=self._playback_allowed, snapshot_resolver=self._resolve_playback_target)
        self._claims = ClaimBridge(client=self._mnemosyne, bank=self._bank)
        self._user_anchor_id = ""
        self._initialized = False
        self._auto_sync_handle = ""

    @property
    def source(self) -> MusicSourceAdapter:
        return self._source

    async def initialize(self) -> EngineStatus:
        from pathlib import Path
        from music_acquisition.util.file_lock import FileLock
        import json
        with FileLock(Path(self._host.local_storage.root()) / "config/.owner.lock"):
            owner = self._host.local_storage.read_json("config/owner.json")
            if owner and (owner.get("owner_user_id") != self._owner_user_id or owner.get("node_id", self._node_id) != self._node_id):
                raise PermissionError("DATA_ROOT_OWNER_MISMATCH")
            if not owner:
                for file in (Path(self._host.local_storage.root()) / "snapshots").rglob("*.json"):
                    prior = json.loads(file.read_text(encoding="utf-8"))
                    if prior.get("owner_user_id") != self._owner_user_id:
                        raise PermissionError("LEGACY_OWNER_MIGRATION_REQUIRED")
            self._host.local_storage.write_json("config/owner.json", {"owner_user_id": self._owner_user_id, "node_id": self._node_id})
        raw_scope = self._host.local_storage.read_json(f"config/scopes_{self._owner_user_id}.json")
        if raw_scope:
            self._gate.configure(MusicScopeConfig.model_validate(raw_scope))
        raw_consent = self._host.local_storage.read_json(f"config/consent_{self._owner_user_id}.json")
        if raw_consent:
            self._gate.set_consent(MusicConsent.model_validate(raw_consent))
        # Restore QR cookie from Memory Bank credentials if present
        try:
            await self._auth.restore_credentials(self._owner_user_id)
        except Exception as exc:  # noqa: BLE001
            self._host.logger.warning("credential_restore_failed", error_code=type(exc).__name__)
        caps = await self._source.get_capabilities()
        auth = await self._source.check_authorization()
        self._initialized = True
        root = self._host.local_storage.root()
        anchor_meta = self._host.local_storage.read_json("config/user_anchor.json") or {}
        self._user_anchor_id = str(anchor_meta.get("memory_id") or "")
        self._host.local_storage.write_json(
            "config/engine.json",
            {
                "version": __version__,
                "node_id": self._node_id,
                "initialized_at": self._host.clock.now_iso(),
                "mnemosyne": bool(self._mnemosyne),
                "memory_bank_kind": getattr(self._bank, "kind", None),
                "user_anchor_id": self._user_anchor_id,
            },
        )
        self._host.logger.info("engine_initialized", data_root=root)
        schedule = self._host.local_storage.read_json("config/auto_sync.json") or {}
        if schedule.get("enabled") and self._gate.consent_active(self._owner_user_id) and auth.status in {"valid", "authorized"}:
            self.enable_auto_sync(interval_sec=schedule.get("interval_sec", 86400), collectors=schedule.get("collectors"), submit=schedule.get("submit", False))
        return EngineStatus(
            ok=True,
            version=__version__,
            auth_status=auth.status,
            initialized=True,
            host=getattr(self._host, "host_name", "host"),
            data_root=root,
            capabilities=caps,
        )

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationChallenge:
        self._ensure_init()
        return await self._auth.authorize(request)

    async def complete_config_bind(
        self,
        session_id: str,
        *,
        favorite_playlist_id: str | int,
        subject_id: str | None = None,
        state_nonce: str | None = None,
    ) -> AuthorizationStatus:
        self._ensure_init()
        session = self._auth._sessions.get(session_id)
        if not session or session.user_id != self._owner_user_id:
            raise PermissionError("SESSION_DENIED")
        from music_acquisition.util.runtime import active_until
        import secrets
        if not active_until(session.expires_at) or session.status in {"cancelled", "expired"} or (state_nonce is not None and not secrets.compare_digest(state_nonce, session.state_nonce)):
            raise PermissionError("SESSION_DENIED")
        bind = getattr(self._source, "complete_config_bind", None)
        if not callable(bind):
            return AuthorizationStatus(
                session_id=session_id,
                status="unsupported",
                detail="source_has_no_config_bind",
            )
        return bind(session_id, favorite_playlist_id=favorite_playlist_id, subject_id=subject_id)

    async def get_authorization_status(
        self, session_id: str | None = None, *, state_nonce=None
    ) -> AuthorizationStatus:
        self._ensure_init()
        return await self._auth.get_status(session_id, state_nonce=state_nonce)

    async def revoke_authorization(self, subject_id: str) -> None:
        self._ensure_init()
        await self._auth.revoke(subject_id)
        self.disable_auto_sync()
        consent = self._gate.consent(self._owner_user_id)
        if consent:
            await self.set_consent(consent.model_copy(update={"status": "revoked"}))

    async def configure_scopes(self, config: MusicScopeConfig) -> None:
        self._ensure_init()
        self._assert_owner(config.owner_user_id)
        self._gate.configure(config)
        old_consent = self._gate.consent(config.owner_user_id)
        if old_consent:
            await self.set_consent(old_consent.model_copy(update={"status": "revoked"}))
        self._host.local_storage.write_json(
            f"config/scopes_{config.owner_user_id}.json",
            config.model_dump(mode="json"),
        )
        audit(self._host, "scopes_changed", owner=config.owner_user_id, scope_count=len(config.scopes))

    def _assert_owner(self, owner):
        if owner != self._owner_user_id:
            raise PermissionError("OWNER_DENIED")

    async def set_consent(self, consent: MusicConsent):
        self._assert_owner(consent.owner_user_id)
        if consent.node_id != self._node_id:
            raise PermissionError("NODE_DENIED")
        self._gate.set_consent(consent)
        self._host.local_storage.write_json(f"config/consent_{self._owner_user_id}.json", consent.model_dump(mode="json"))
        audit(self._host, "consent_changed", owner=self._owner_user_id, consent_id=consent.consent_id, status=consent.status)
        return {"ok": True}

    def _require_purpose(self, purpose):
        raw = self._host.local_storage.read_json(f"config/consent_{self._owner_user_id}.json")
        if raw:
            self._gate.set_consent(MusicConsent.model_validate(raw))
        if not self._gate.allows(self._owner_user_id, purpose=purpose):
            raise PermissionError("PURPOSE_OR_CONSENT_DENIED")

    def _playback_allowed(self):
        raw = self._host.local_storage.read_json(f"config/consent_{self._owner_user_id}.json")
        config = self._host.local_storage.read_json(f"config/scopes_{self._owner_user_id}.json")
        if raw:
            self._gate.set_consent(MusicConsent.model_validate(raw))
        if config:
            self._gate.configure(MusicScopeConfig.model_validate(config))
        consent = self._gate.consent(self._owner_user_id)
        return bool(consent and self._gate.consent_active(self._owner_user_id) and "music.playback.control" in consent.scopes
            and "music.playback.control" in self._gate.get_scopes(self._owner_user_id)
            and not (self._host.local_storage.read_json(f"auth/revoked_{self._owner_user_id}.json") or {}).get("revoked"))

    def _resolve_playback_target(self, kind, *, playlist_id=None, date=None, track_id=None, index=None, current_track_id=None, delta=1):
        self._require_purpose("music.data.read")
        from music_acquisition.util.runtime import active_until
        normalize = lambda value: str(value or "").removeprefix("netease:")
        binding = self._host.local_storage.read_json(f"auth/credential_index_{self._owner_user_id}.json") or {}
        subject = binding.get("subject_id") or getattr(self._source, "subject_id", getattr(self._source, "_subject_id", ""))
        today = current_local_date(self._host.clock)
        if kind == "daily":
            if date != today:
                return {"ok": False, "error": "SNAPSHOT_EXPIRED"}
            snap = self._snapshots.load("daily_songs", subject, date)
            scope = {"kind": "daily", "date": date}
        else:
            snap = self._snapshots.load("playlist_tracks", subject)
            if not snap and normalize(playlist_id) == normalize(getattr(self._source, "favorite_playlist_id", "")):
                snap = self._snapshots.load("liked_songs", subject)
            scope = {"kind": "playlist", "playlist_id": playlist_id}
        if not snap or not snap.complete or snap.status != "complete":
            return {"ok": False, "error": "SNAPSHOT_INCOMPLETE"}
        if not active_until(snap.expires_at):
            return {"ok": False, "error": "SNAPSHOT_EXPIRED"}
        rows = snap.records
        if snap.data_type == "playlist_tracks":
            rows = [r for r in rows if normalize(r.get("parent_playlist_id")) == normalize(playlist_id)]
        ids = [normalize(r.get("provider_track_id") or r.get("track_id")) for r in rows]
        if not ids:
            return {"ok": False, "error": "EMPTY_PLAYLIST"}
        if current_track_id is not None:
            if normalize(current_track_id) not in ids:
                return {"ok": False, "error": "PLAY_SOURCE_MISMATCH"}
            position = (ids.index(normalize(current_track_id)) + int(delta)) % len(ids)
        elif track_id is not None:
            if normalize(track_id) not in ids:
                return {"ok": False, "error": "TRACK_NOT_IN_PLAYLIST"}
            position = ids.index(normalize(track_id))
        else:
            position = int(index or 1) - 1
            if position < 0 or position >= len(ids):
                return {"ok": False, "error": "INDEX_OUT_OF_RANGE"}
        return {"ok": True, "track_id": ids[position], "index": position + 1, "snapshot_id": snap.snapshot_id, "scope": scope}

    async def sync_now(self, request: SyncRequest) -> SyncRun:
        self._ensure_init()
        self._assert_owner(request.owner_user_id)
        binding = self._host.local_storage.read_json(f"auth/credential_index_{self._owner_user_id}.json") or {}
        subject = binding.get("subject_id") or getattr(self._source, "subject_id", getattr(self._source, "_subject_id", "unknown"))
        # Capture previous liked snapshot for revoke diff (before sync overwrites)
        prev_liked = self._snapshots.load("liked_songs", subject)
        run = await self._sync.sync_now(request)
        subject = run.subject_id or subject
        if run.next_retry_at:
            self._schedule_recovery(run.run_id, run.next_retry_at)
        revoke_summary: dict | None = None
        for dt in run.collectors:
            date = current_local_date(self._host.clock) if dt in {"daily_songs", "daily_playlists"} else None
            snap = self._snapshots.load(dt, subject, date)
            snaps = [snap] if snap and snap.snapshot_id in run.snapshot_ids and snap.complete and run.collector_results.get(dt, {}).get("status") == "success" else []
            for snap in snaps:
                # Authorized sync may propose candidate memories for saved tracks only
                # (never auto likes / confirmed preference).
                allowed = self._gate.allows(self._owner_user_id, purpose="music.data.submit_evidence")
                items = self._memory.map_snapshot_to_evidence(snap, allow_create_memory=self._gate.allows(self._owner_user_id, purpose="music.data.create_memory")) if allowed else []
                if items:
                    self._outbox.enqueue(items, idempotency_key=f"snapshot:{snap.snapshot_id}")
            # Incremental unfavorite / removed tracks → revoke evidence + supersede saves
            if dt == "liked_songs" and prev_liked and snaps and self._gate.allows(self._owner_user_id, purpose="music.data.submit_evidence"):
                cur = snaps[0]
                diff = self._snapshots.diff_against_previous(previous=prev_liked, current=cur)
                revoke_items = diff_to_revoke_evidence(diff)
                if revoke_items:
                    self._outbox.enqueue(
                        revoke_items, idempotency_key=f"{run.run_id}:revoke:{diff.diff_id}"
                    )
                    revoke_summary = apply_revokes(
                        self._bank,
                        revoke_items,
                        user_anchor_id=self._user_anchor_id,
                        dry_run=not bool(self._bank) or not self._gate.allows(self._owner_user_id, purpose="music.data.create_memory"),
                    )
                    self._host.local_storage.write_json(
                        f"sync/last_revoke_{run.run_id}.json",
                        {"diff": diff.model_dump(mode="json"), "revoke": revoke_summary},
                    )
        if revoke_summary is not None:
            # attach lightly on run via host log (SyncRun model may not have extra field)
            self._host.logger.info(
                "sync_revoke_applied",
                run_id=run.run_id,
                count=revoke_summary.get("count"),
            )
        return run

    def _schedule_recovery(self, run_id, at):
        from music_acquisition.util.runtime import parse_time
        delay = max(0.1, (parse_time(at) - parse_time(self._host.clock.now_iso())).total_seconds())
        def retry():
            import asyncio
            if self._initialized:
                asyncio.run(self.recover_startup(self._owner_user_id))
        self._host.task_scheduler.schedule("retry_" + run_id, delay, retry)

    async def recover_startup(self, owner_user_id: str) -> list[SyncRun]:
        self._ensure_init()
        self._assert_owner(owner_user_id)
        from music_acquisition.util.runtime import parse_time
        for rid in list_pending_run_ids(self._host):
            row = self._host.local_storage.read_json(f"sync/runs/{rid}.json") or {}
            if row.get("owner_user_id") == owner_user_id and row.get("next_retry_at") and parse_time(row["next_retry_at"]) > parse_time(self._host.clock.now_iso()):
                self._schedule_recovery(rid, row["next_retry_at"])
        runs: list[SyncRun] = []
        for req in startup_recovery_requests(self._host, owner_user_id):
            runs.append(await self.sync_now(req))
        return runs

    async def get_sync_status(self, run_id: str | None = None) -> SyncStatus:
        self._ensure_init()
        return self._sync.get_status(run_id)

    async def get_snapshots(
        self, *, data_type: str, subject_id: str, local_date: str | None = None
    ) -> list[MusicSnapshot]:
        self._ensure_init()
        self._require_purpose("music.data.read")
        if data_type in {"daily_songs", "daily_playlists"} and local_date is None:
            local_date = current_local_date(self._host.clock)
        snap = self._snapshots.load(data_type, subject_id, local_date)
        if not snap:
            return []
        if snap.owner_user_id != self._owner_user_id:
            raise PermissionError("OWNER_DENIED")
        self._require_purpose("music.data.read")
        snap = mark_expired_if_needed(snap, tz_name=self._host.clock.timezone(), today=current_local_date(self._host.clock))
        return [snap]

    async def get_diff(
        self,
        *,
        data_type: str,
        subject_id: str,
        previous: MusicSnapshot,
        current: MusicSnapshot,
    ) -> MusicDiff:
        self._ensure_init()
        self._require_purpose("music.data.read")
        return self._snapshots.diff_against_previous(previous=previous, current=current)

    def map_to_evidence(self, snapshot: MusicSnapshot, **kwargs):
        return self._memory.map_snapshot_to_evidence(snapshot, **kwargs)

    def enqueue_evidence(self, items: list) -> str:
        self._require_purpose("music.data.submit_evidence")
        return self._outbox.enqueue(items)

    def submit_evidence_outbox(
        self,
        *,
        promote_graph: bool = False,
        publish_preference_signals: bool = False,
        dry_run_graph: bool = False,
        user_anchor_memory_id: str = "",
    ) -> dict:
        """Submit outbox observations; optionally promote graph / inferred prefs (never likes)."""
        self._require_purpose("music.data.submit_evidence")
        if promote_graph or publish_preference_signals:
            self._require_purpose("music.data.create_memory")
        submit = self._submitter.submit_pending()
        pending_items: list = []
        for batch in self._outbox.list_pending():
            pending_items.extend(batch.get("items") or [])
        # Also gather just-submitted batches from local storage index (best-effort)
        # Prefer items from submit path: re-read submitted files if needed
        submitted_items = self._collect_recent_evidence_items()
        items = submitted_items or pending_items
        graph = None
        prefs = None
        if promote_graph:
            graph = self._graph.promote_evidence(
                items,
                dry_run=dry_run_graph or not bool(self._mnemosyne),
                link_user_anchor=user_anchor_memory_id or None,
            )
        if publish_preference_signals:
            prefs = self._prefs.publish(
                items,
                dry_run=dry_run_graph or not bool(self._mnemosyne),
            )
        return {
            **submit,
            "graph": graph,
            "preference_signals": prefs,
        }

    def _collect_recent_evidence_items(self) -> list:
        index = self._host.local_storage.read_json("memory/outbox/_index.json") or {"ids": []}
        items: list = []
        for oid in list(index.get("ids") or [])[-20:]:
            raw = self._host.local_storage.read_json(f"memory/outbox/{oid}.json")
            if not raw:
                continue
            if raw.get("status") in ("pending", "submitted"):
                items.extend(raw.get("items") or [])
        return items

    def register_music_domain(self, *, pack_path: str = "") -> dict:
        """Install-time equivalent: register Music Domain Pack via Mnemosyne API.

        Always loads the pack locally and POSTs inline JSON. Never relies on Node
        being able to read a host filesystem ``pack_path``.
        """
        import json
        from pathlib import Path

        candidates: list[Path] = [Path(__file__).resolve().parents[1] / "resources" / "music_domain_pack.v1.json"]
        if pack_path:
            candidates.append(Path(pack_path))
        candidates.extend(
            [
                Path(__file__).resolve().parents[3] / "domain" / "music_domain_pack.v1.json",
                Path(__file__).resolve().parents[2].parent / "domain" / "music_domain_pack.v1.json",
            ]
        )
        pack: dict | None = None
        used = ""
        for cand in candidates:
            if cand.is_file():
                try:
                    pack = json.loads(cand.read_text(encoding="utf-8"))
                    used = str(cand)
                    break
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": "pack_read_failed", "detail": str(exc), "pack_path": str(cand)}
        if pack is None:
            return {"ok": False, "error": "pack_not_found", "tried": [str(c) for c in candidates]}

        if not self._mnemosyne:
            return {
                "ok": True,
                "local_only": True,
                "pack_path": used,
                "domain_id": pack.get("domain_id"),
                "note": (
                    "mnemosyne_not_configured; pack loaded locally only. "
                    "Configure Mnemosyne URL to POST /api/v1/domains/register."
                ),
            }
        result = self._mnemosyne.register_domain_pack(pack, pack_path=used)
        if self._bank and hasattr(self._bank, "register_domain_pack"):
            # Keep bank backend path consistent when dual-wrapped.
            pass
        return {**result, "pack_path": used}

    def promote_music_graph(self, *, dry_run: bool = True, user_anchor_memory_id: str = "") -> dict:
        self._require_purpose("music.data.create_memory")
        items = self._collect_recent_evidence_items()
        anchor = user_anchor_memory_id or self._user_anchor_id
        if not anchor and self._bank:
            self.ensure_user_anchor()
            anchor = self._user_anchor_id
        return self._graph.promote_evidence(
            items,
            dry_run=dry_run or not bool(self._mnemosyne),
            link_user_anchor=anchor or None,
        )

    def publish_preference_signals(self, *, dry_run: bool = True) -> dict:
        self._require_purpose("music.data.create_memory")
        items = self._collect_recent_evidence_items()
        return self._prefs.publish(items, dry_run=dry_run or not bool(self._mnemosyne))

    def ensure_user_anchor(self, *, subject_key: str = "", force: bool = False) -> dict:
        """Auto-bind User endpoint memory id (idempotent).

        Rejects stale ``lf_*`` anchors when the active bank is Mnemosyne HTTP.
        """
        self._ensure_init()
        self._require_purpose("music.data.create_memory")
        if not self._bank:
            return {"ok": False, "error": "memory_bank_unavailable"}
        key = subject_key or self._owner_user_id or "self"
        kind = str(getattr(self._bank, "kind", "") or "")
        stale_local = bool(self._user_anchor_id.startswith("lf_")) and kind == "mnemosyne"
        if self._user_anchor_id and not force and not stale_local:
            return {"ok": True, "id": self._user_anchor_id, "reused": True}
        if stale_local:
            self._user_anchor_id = ""
        result = self._bank.ensure_user_anchor(subject_key=key)
        mid = str(result.get("id") or "")
        if mid:
            self._user_anchor_id = mid
            self._host.local_storage.write_json(
                "config/user_anchor.json",
                {
                    "memory_id": mid,
                    "subject_key": key,
                    "bank_kind": getattr(self._bank, "kind", ""),
                    "updated_at": self._host.clock.now_iso(),
                },
            )
        return result

    def confirm_like(
        self,
        *,
        target_id: str,
        title: str = "",
        target_type: str = "track",
        evidence_refs: list | None = None,
        memory_id: str = "",
        note: str = "",
        dry_run: bool = False,
    ) -> dict:
        """Explicit user confirm → likes (requires user_anchor)."""
        self._ensure_init()
        self._require_purpose("music.data.create_memory")
        if not self._bank:
            if dry_run:
                return {"ok": True, "dry_run": True, "submitted": False}
            return self._pending_operation("confirm_like", target_id=target_id, title=title, target_type=target_type, note=note, evidence_refs=evidence_refs or [])
        if not self._user_anchor_id and not dry_run:
            self.ensure_user_anchor()
        if not self._user_anchor_id and not dry_run:
            return self._pending_operation("confirm_like", target_id=target_id, title=title, target_type=target_type, evidence_refs=evidence_refs, memory_id=memory_id, note=note)
        result = confirm_preference(
            self._bank,
            user_anchor_id=self._user_anchor_id or "(pending_user_anchor)",
            target_type=target_type,
            target_id=target_id,
            title=title,
            evidence_refs=evidence_refs,
            memory_id=memory_id or None,
            note=note,
            dry_run=dry_run,
            claim_bridge=self._claims,
        )

        if not result.get("ok") and not dry_run:
            return self._pending_operation("confirm_like", target_id=target_id, title=title, target_type=target_type, evidence_refs=evidence_refs, memory_id=memory_id, note=note)
        return result

    def reject_preference_signal(
        self,
        *,
        memory_id: str = "",
        target_id: str = "",
        note: str = "",
        dry_run: bool = False,
    ) -> dict:
        self._ensure_init()
        self._require_purpose("music.data.create_memory")
        if not self._bank:
            if dry_run:
                return {"ok": True, "dry_run": True, "submitted": False}
            return self._pending_operation("reject_preference_signal", target_id=target_id, memory_id=memory_id, note=note)
        result = reject_preference(
            self._bank,
            memory_id=memory_id,
            target_id=target_id,
            note=note,
            dry_run=dry_run,
        )


        if not result.get("ok") and not dry_run:
            return self._pending_operation("reject_preference_signal", target_id=target_id, memory_id=memory_id, note=note)
        return result

    def unconfirm_like(
        self,
        *,
        target_id: str = "",
        title: str = "",
        target_type: str = "track",
        memory_id: str = "",
        note: str = "",
        dry_run: bool = False,
    ) -> dict:
        """Explicit revoke of confirmed likes (does not touch NetEase saves)."""
        self._ensure_init()
        self._require_purpose("music.data.create_memory")
        if not self._bank:
            if dry_run:
                return {"ok": True, "dry_run": True, "submitted": False}
            return self._pending_operation("unconfirm_like", target_id=target_id, title=title, target_type=target_type, note=note, memory_id=memory_id)
        if not self._user_anchor_id and not dry_run:
            self.ensure_user_anchor()
        result = unconfirm_preference(
            self._bank,
            user_anchor_id=self._user_anchor_id,
            target_type=target_type,
            target_id=target_id,
            title=title,
            memory_id=memory_id,
            note=note,
            dry_run=dry_run,
            claim_bridge=self._claims,
        )

        if not result.get("ok") and not dry_run:
            return self._pending_operation("unconfirm_like", target_id=target_id, title=title, target_type=target_type, note=note, memory_id=memory_id)
        return result

    def monitor_snapshot(self) -> dict:
        """Minimal ops snapshot for Monitor UI (sync-safe)."""
        self._ensure_init()
        from music_acquisition.bank_paths import bank_layout_status

        pending = self._outbox.list_pending()
        auth: dict = {"status": "unknown"}
        try:
            # Prefer sync check on source to avoid nested event loops
            import asyncio

            check = self._source.check_authorization()
            if asyncio.iscoroutine(check):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    auth = {"status": "pending_async"}
                    check.close()
                else:
                    auth = asyncio.run(check).model_dump(mode="json")
            else:
                auth = check.model_dump(mode="json")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            auth = {"status": "error", "detail": str(exc)}
        bank_health = self._bank.health() if self._bank else {"ok": False}
        # latest revoke audit file if any
        last_revoke = None
        try:
            from pathlib import Path

            sync_dir = Path(self._host.local_storage.root()) / "sync"
            files = sorted(sync_dir.glob("last_revoke_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                import json

                last_revoke = json.loads(files[0].read_text(encoding="utf-8"))
        except Exception:
            last_revoke = None
        return {
            "ok": True,
            "version": __version__,
            "metrics": self.metrics(),
            "auth": auth,
            "user_anchor_id": self._user_anchor_id,
            "memory_bank": bank_health,
            "bank_layout": bank_layout_status(),
            "outbox_pending": len(pending),
            "outbox_ids": [b.get("outbox_id") for b in pending[:20]],
            "playlist_scopes": self._scope_store.snapshot(),
            "engine_config": self._host.local_storage.read_json("config/engine.json") or {},
            "last_revoke": last_revoke,
        }

    async def bootstrap_pipeline(
        self,
        *,
        owner_user_id: str = "",
        mode: str = "config_bind",
        favorite_playlist_id: str = "",
        subject_id: str = "",
        collectors: list[str] | None = None,
        promote_graph: bool = True,
        preference_signals: bool = True,
        commit: bool = False,
        wait_qr_sec: float = 0,
    ) -> dict:
        """One-shot: register domain → user_anchor → auth → sync → submit/promote.

        ``mode``: ``config_bind`` | ``qr`` (qr waits ``wait_qr_sec`` polling).
        """
        self._ensure_init()
        owner = owner_user_id or self._owner_user_id
        self._assert_owner(owner)
        self._require_purpose("music.data.read")
        steps: dict = {}

        steps["domain"] = self.register_music_domain()
        steps["user_anchor"] = self.ensure_user_anchor(subject_key=owner)

        from music_acquisition.domain.models import AuthorizationRequest, MusicScopeConfig, SyncRequest


        if mode == "qr":
            ch = await self.authorize(AuthorizationRequest(owner_user_id=owner, node_id=self._node_id, mode="config_bind"))
            steps["auth_challenge"] = ch.model_dump(mode="json")
            import asyncio

            deadline = asyncio.get_event_loop().time() + max(0.0, float(wait_qr_sec))
            status = await self.get_authorization_status(ch.session_id)
            while wait_qr_sec > 0 and status.status not in ("authorized", "valid", "expired", "failed"):
                if asyncio.get_event_loop().time() >= deadline:
                    break
                await asyncio.sleep(2.0)
                status = await self.get_authorization_status(ch.session_id)
            steps["auth"] = status.model_dump(mode="json")
        else:
            ch = await self.authorize(AuthorizationRequest(owner_user_id=owner, node_id=self._node_id, mode="config_bind"))
            if favorite_playlist_id:
                bind = await self.complete_config_bind(
                    ch.session_id,
                    favorite_playlist_id=favorite_playlist_id,
                    subject_id=subject_id or None,
                )
                steps["auth"] = bind.model_dump(mode="json")
            else:
                # try restore cookie only
                restored = await self._auth.restore_credentials(owner)
                st = await self.get_authorization_status()
                steps["auth"] = {**st.model_dump(mode="json"), "cookie_restored": restored}

        cols = collectors or [
            "liked_songs",
            "playlists",
            "daily_songs",
            "playback_history",
            "favorite_artists",
        ]
        run = await self.sync_now(SyncRequest(owner_user_id=owner, collectors=cols))
        steps["sync"] = run.model_dump(mode="json")

        submit = self.submit_evidence_outbox(
            promote_graph=promote_graph,
            publish_preference_signals=preference_signals,
            dry_run_graph=not commit,
            user_anchor_memory_id=self._user_anchor_id,
        )
        steps["submit"] = submit
        steps["playlist_scopes"] = self.sync_playlist_scopes_from_client()
        domain_ok = bool(steps.get("domain", {}).get("ok"))
        # When Mnemosyne is configured, domain registration failure must fail bootstrap.
        if self._mnemosyne and not domain_ok:
            domain_ok = False
        elif not self._mnemosyne:
            domain_ok = bool(steps.get("domain", {}).get("ok", True))
        ok = (
            run.status in ("success", "partial")
            and bool(steps.get("user_anchor", {}).get("ok", True))
            and domain_ok
        )
        return {
            "ok": ok,
            "version": __version__,
            "user_anchor_id": self._user_anchor_id,
            "memory_bank_kind": getattr(self._bank, "kind", None),
            "domain_ok": domain_ok,
            "steps": steps,
            "warnings": (
                []
                if domain_ok
                else ["domain_register_failed — ontology may be missing on Node"]
            ),
        }

    def recall(self, *, subject_id: str, query: str = "") -> dict:
        self._require_purpose("music.data.recall")
        snaps: list[MusicSnapshot] = []
        for dt in ("liked_songs", "daily_songs", "playlists"):
            date = current_local_date(self._host.clock) if dt == "daily_songs" else None
            s = self._snapshots.load(dt, subject_id, date)
            if s and s.owner_user_id == self._owner_user_id:
                snaps.append(s)
        return self._recall.recall_from_snapshots(snaps, query=query)

    def recall_mnemosyne(self, query: str) -> dict:
        self._require_purpose("music.data.recall")
        return self._recall.recall_with_mnemosyne(query)

    def _pending_operation(self, operation, **payload):
        import hashlib, json
        from pathlib import Path
        from music_acquisition.util.file_lock import FileLock
        oid = "op_" + hashlib.sha256(json.dumps([self._owner_user_id, operation, payload], sort_keys=True).encode()).hexdigest()[:24]
        folder = Path(self._host.local_storage.root()) / "memory/operations"
        with FileLock(folder / "_operations.lock"):
            sequence = (self._host.local_storage.read_json("memory/operations/_sequence.json") or {}).get("value", 0) + 1
            self._host.local_storage.write_json("memory/operations/_sequence.json", {"value": sequence})
            target = payload.get("target_id") or payload.get("memory_id")
            for path in folder.glob("op_*.json"):
                old = self._host.local_storage.read_json(f"memory/operations/{path.name}") or {}
                prior = old.get("payload") or {}
                if target and old.get("status") == "pending" and target == (prior.get("target_id") or prior.get("memory_id")):
                    old["status"] = "superseded"
                    self._host.local_storage.write_json(f"memory/operations/{path.name}", old)
            self._host.local_storage.write_json(f"memory/operations/{oid}.json", {"operation": operation,
                "payload": payload, "owner_user_id": self._owner_user_id, "status": "pending", "sequence": sequence,
                "created_at": self._host.clock.now_iso()})
        return {"ok": False, "status": "pending", "operation_id": oid, "submitted": False}

    def submit_operations(self):
        self._require_purpose("music.data.create_memory")
        from pathlib import Path
        import json
        results = []
        if not self._bank:
            return {"ok": False, "error": "mnemosyne_not_configured"}
        for path in sorted((Path(self._host.local_storage.root()) / "memory" / "operations").glob("*.json")):
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("status") != "pending" or row.get("owner_user_id") != self._owner_user_id:
                continue
            if row.get("operation") not in {"confirm_like", "unconfirm_like", "reject_preference_signal"}:
                continue
            result = getattr(self, row["operation"])(**row["payload"])
            if result.get("ok"):
                row["status"] = "submitted"
                self._host.local_storage.write_json(f"memory/operations/{path.name}", row)
            results.append({"operation_id": path.stem, "ok": bool(result.get("ok"))})
        return {"ok": all(r["ok"] for r in results), "results": results}

    def sync_history(self):
        from pathlib import Path
        import json
        items = [json.loads(p.read_text(encoding="utf-8")) for p in (Path(self._host.local_storage.root()) / "sync" / "runs").glob("*.json")]
        return {"items": sorted(items, key=lambda r: r.get("started_at", ""), reverse=True)[:100]}

    def metrics(self):
        from pathlib import Path
        from collections import Counter
        import json
        root = Path(self._host.local_storage.root())
        runs = self.sync_history()["items"]
        counts = Counter()
        for path in (root / "snapshots").rglob("*.json"):
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            counts[snapshot.get("status", "unknown")] += 1
        return {"sync_runs": len(runs), "sync_statuses": dict(Counter(r.get("status", "unknown") for r in runs)),
            "retry_count": sum(r.get("retry_count", 0) for r in runs), "snapshots": dict(counts),
            "pending_runs": len(list_pending_run_ids(self._host)), "outbox_pending": len(self._outbox.list_pending()),
            "playback_capabilities": self._playback.capabilities(), "scheduler": self.scheduler_snapshot()}

    def export_data(self):
        self._require_purpose("music.data.export")
        from pathlib import Path
        import json
        root = Path(self._host.local_storage.root())
        snapshots = [json.loads(p.read_text(encoding="utf-8")) for p in (root / "snapshots").rglob("*.json")]
        diffs = [json.loads(p.read_text(encoding="utf-8")) for p in (root / "diffs").glob("*.json")]
        audit(self._host, "data_exported", owner=self._owner_user_id, count=len(snapshots))
        return {"owner_user_id": self._owner_user_id, "snapshots": snapshots, "diffs": diffs, "credentials_included": False, "node_data_included": False}

    def delete_data(self, *, target, confirm=False, object_id=""):
        self._require_purpose("music.data.delete")
        from pathlib import Path
        import re
        root = Path(self._host.local_storage.root()).resolve()
        locations = {"snapshots": ["snapshots", "pages", "diffs"], "outbox": ["memory/outbox"], "pending_operations": ["memory/operations"]}
        if target in {"node_memory", "node_evidence"}:
            if not self._mnemosyne or not re.fullmatch(r"[A-Za-z0-9_-]+", object_id):
                return {"ok": False, "error": "NODE_OBJECT_REQUIRED"}
            endpoint = "memories" if target == "node_memory" else "evidences"
            record = self._mnemosyne._request("GET", f"/api/v1/{endpoint}/{object_id}")
            meta = record.get("metadata") or (record.get("evidence") or {}).get("metadata") or {}
            if meta.get("domain") != "music":
                return {"ok": False, "error": "MUSIC_OBJECT_REQUIRED"}
            if not confirm:
                return {"ok": True, "preview": True, "target": target, "object_id": object_id}
            if target == "node_evidence":
                # Node exposes retraction, not a promise of physical evidence erasure.
                result = self._mnemosyne._request("POST", f"/api/v1/evidences/{object_id}/assessment", {"state": "revoked"})
                return {**result, "physical_erasure": False, "operation": "revoke_evidence"}
            return self._mnemosyne._request("DELETE", f"/api/v1/memories/{object_id}")
        if target not in locations:
            raise ValueError("unknown_delete_target")
        files = []
        for location in locations[target]:
            for file in (root / location).rglob("*.json"):
                resolved = file.resolve()
                if root not in resolved.parents:
                    raise ValueError("path_outside_data_root")
                files.append(file)
        if confirm:
            # File-level deletion, constrained to the explicit acquisition subtree.
            for file in files:
                file.unlink()
            audit(self._host, "data_deleted", owner=self._owner_user_id, target=target, count=len(files))
        return {"ok": True, "preview": not confirm, "target": target, "count": len(files), "node_data_deleted": False}

    def cleanup(self, *, retention_days=30, confirm=False):
        self._require_purpose("music.data.delete")
        from pathlib import Path
        from datetime import timedelta
        from music_acquisition.util.runtime import parse_time
        import json
        if not 1 <= retention_days <= 3650:
            raise ValueError("invalid_retention_days")
        root = Path(self._host.local_storage.root()).resolve()
        consent = self._gate.consent(self._owner_user_id)
        policies = self._host.local_storage.read_json("config/retention.json") or {}
        policy = policies.get(consent.retention_policy_id, {"snapshot_days": 30, "outbox_days": 90, "audit_days": 180})
        files = []
        for tree in ("snapshots", "pages", "diffs", "memory/outbox", "audit"):
            category = "outbox_days" if tree == "memory/outbox" else "audit_days" if tree == "audit" else "snapshot_days"
            cutoff = parse_time(self._host.clock.now_iso()) - timedelta(days=min(retention_days, policy.get(category, 30)))
            for path in (root / tree).rglob("*.json"):
                if root not in path.resolve().parents:
                    raise ValueError("path_outside_data_root")
                record = json.loads(path.read_text(encoding="utf-8"))
                at = record.get("fetched_at") or record.get("generated_at") or record.get("created_at") or record.get("at")
                if at and parse_time(at) < cutoff:
                    files.append(path)
        if confirm:
            for path in files:
                path.unlink()
            audit(self._host, "retention_cleanup", owner=self._owner_user_id, count=len(files), retention_days=retention_days)
        return {"ok": True, "preview": not confirm, "count": len(files), "node_data_deleted": False}

    def set_retention_policy(self, name, policy):
        fields = {"snapshot_days", "outbox_days", "audit_days"}
        if not name or set(policy) != fields or any(type(v) is not int or not 1 <= v <= 3650 for v in policy.values()):
            raise ValueError("invalid_retention_policy")
        policies = self._host.local_storage.read_json("config/retention.json") or {}
        policies[name] = policy
        self._host.local_storage.write_json("config/retention.json", policies)
        audit(self._host, "retention_policy_changed", owner=self._owner_user_id, policy_id=name)
        return {"ok": True, "policy_id": name, **policy}

    @property
    def playback(self) -> MusicPlaybackService:
        return self._playback

    @property
    def playlist_scopes(self) -> PlaylistScopeStore:
        return self._scope_store

    def playlist_roundtrip(
        self,
        *,
        track_id: str | None = None,
        scope_key: str | None = None,
        index_1based: int | None = None,
        include_runtime: bool = False,
    ) -> dict:
        """Memory Bank 往返查找：track ↔ (源歌单, 序号)。"""
        return self._scope_store.roundtrip(
            track_id=track_id,
            scope_key=scope_key,
            index_1based=index_1based,
            include_runtime=include_runtime,
        )

    def sync_playlist_scopes_from_client(self) -> dict:
        """从桌宠 CDP/进程表灌入 Bank，并可选回灌进程表。"""
        self._require_purpose("music.data.read")
        self._require_purpose("music.data.cache")
        ingested = self._scope_store.ingest_from_murasame_registry()
        # 若桌宠不可用，仍返回 Bank 快照
        return {
            "ok": bool(ingested.get("ok")),
            "ingest": ingested,
            "snapshot": self._scope_store.snapshot(),
            "mnemosyne": bool(self._mnemosyne),
            "data_root": self._host.local_storage.root(),
        }

    def hydrate_playlist_scopes_to_client(self) -> dict:
        self._require_purpose("music.data.export")
        return self._scope_store.hydrate_murasame_registry()

    def enable_auto_sync(
        self,
        *,
        interval_sec: float = 86400.0,
        collectors: list[str] | None = None,
        submit: bool = True,
        promote_graph: bool = False,
        initial_delay_sec: float = 5.0,
    ) -> dict:
        """Schedule recurring sync+submit via HostRuntime.task_scheduler.

        Requires a real scheduler (FilesystemHost ThreadingScheduler). Returns
        handle; cancel with ``disable_auto_sync``.
        """
        self._ensure_init()
        if interval_sec < 1:
            raise ValueError("invalid_schedule_interval")
        self._require_purpose("music.data.read")
        self._require_purpose("music.data.cache")
        if submit:
            self._require_purpose("music.data.submit_evidence")
        sched = self._host.task_scheduler
        every = getattr(sched, "schedule_every", None)
        cols = collectors or [
            "liked_songs",
            "playlists",
            "daily_songs",
            "playback_history",
            "favorite_artists",
        ]

        def _job() -> None:
            import asyncio

            from music_acquisition.domain.models import SyncRequest

            async def _run() -> None:
                try:
                    await self.recover_startup(self._owner_user_id)
                    await self.sync_now(
                        SyncRequest(owner_user_id=self._owner_user_id, collectors=cols, trigger="scheduled")
                    )
                    if submit:
                        self.submit_evidence_outbox(
                            promote_graph=promote_graph,
                            publish_preference_signals=False,
                            dry_run_graph=True,
                        )
                except Exception as exc:  # noqa: BLE001
                    self._host.logger.warning("auto_sync_failed", error=str(exc))

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_run())
                else:
                    loop.run_until_complete(_run())
            except RuntimeError:
                asyncio.run(_run())

        if callable(every):
            handle = every(
                "music_auto_sync",
                float(interval_sec),
                _job,
                initial_delay_sec=float(initial_delay_sec),
            )
        else:
            handle = sched.schedule("music_auto_sync", float(initial_delay_sec), _job)
        self._auto_sync_handle = str(handle)
        meta = {
            "enabled": True,
            "handle": self._auto_sync_handle,
            "interval_sec": interval_sec,
            "collectors": cols,
            "submit": submit,
            "scheduler": type(sched).__name__,
            "enabled_at": self._host.clock.now_iso(),
        }
        self._host.local_storage.write_json("config/auto_sync.json", meta)
        self._host.logger.info("auto_sync_enabled", **meta)
        return {"ok": True, **meta}

    def disable_auto_sync(self) -> dict:
        handle = self._auto_sync_handle or "music_auto_sync"
        try:
            self._host.task_scheduler.cancel(handle)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        self._auto_sync_handle = ""
        self._host.local_storage.write_json(
            "config/auto_sync.json", {"enabled": False, "disabled_at": self._host.clock.now_iso()}
        )
        return {"ok": True, "handle": handle}

    def scheduler_snapshot(self) -> dict:
        snap = getattr(self._host.task_scheduler, "snapshot", None)
        return snap() if callable(snap) else {"ok": False, "error": "no_snapshot"}

    async def shutdown(self) -> None:
        try:
            self._host.task_scheduler.cancel(self._auto_sync_handle or "music_auto_sync")
            self._auto_sync_handle = ""
        except Exception:
            pass
        host_shutdown = getattr(self._host, "shutdown", None)
        if callable(host_shutdown):
            host_shutdown()
        self._initialized = False
        self._host.logger.info("engine_shutdown")

    def _ensure_init(self) -> None:
        if not self._initialized:
            raise RuntimeError("engine_not_initialized")
