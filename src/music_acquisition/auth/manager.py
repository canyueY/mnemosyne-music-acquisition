# -*- coding: utf-8 -*-
"""Auth Manager — QR session lifecycle; credentials never logged."""

from __future__ import annotations

import secrets
from music_acquisition.util.runtime import active_until, audit

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.contracts.source_adapter import MusicSourceAdapter
from music_acquisition.domain.models import (
    AuthorizationChallenge,
    AuthorizationRequest,
    AuthorizationSession,
    AuthorizationStatus,
)


class AuthManager:
    def __init__(self, host: HostRuntime, source: MusicSourceAdapter, *, owner_user_id="owner") -> None:
        self._host = host
        self._source = source
        self._sessions: dict[str, AuthorizationSession] = {}
        self.owner = owner_user_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationChallenge:
        if request.owner_user_id != self.owner:
            raise PermissionError("OWNER_DENIED")
        begin = getattr(self._source, "begin_config_bind", None) if request.mode == "config_bind" else None
        challenge = await (begin() if callable(begin) else self._source.begin_authorization())
        nonce = secrets.token_urlsafe(16)
        session = AuthorizationSession(
            session_id=challenge.session_id,
            provider=request.provider,
            node_id=request.node_id,
            user_id=request.owner_user_id,
            state_nonce=nonce,
            status="waiting_scan",
            created_at=self._host.clock.now_iso(),
            expires_at=challenge.expires_at or "",
        )
        self._sessions[session.session_id] = session
        self._host.local_storage.write_json(
            f"auth/sessions/{session.session_id}.json",
            session.model_dump(mode="json"),
        )
        await self._host.user_interaction.show_qr(
            session.session_id,
            challenge.qr_url or challenge.qr_key,
            message="Scan to authorize NetEase Music (no password to Agent)",
        )
        self._host.logger.info(
            "auth_session_created",
            session_id=session.session_id,
            # never log credential material
        )
        audit(self._host, "authorization_started", owner=self.owner, session_id=session.session_id)
        qr_image = None
        if challenge.qr_url:
            try:
                import qrcode, io, base64
                output = io.BytesIO()
                qrcode.make(challenge.qr_url).save(output, format="PNG")
                qr_image = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
            except ImportError:
                pass
        return challenge.model_copy(update={"state_nonce": nonce, "qr_image_data": qr_image})

    async def get_status(self, session_id: str | None = None, *, state_nonce=None) -> AuthorizationStatus:
        revoked = self._host.local_storage.read_json(f"auth/revoked_{self.owner}.json") or {}
        if revoked.get("revoked") and not session_id:
            return AuthorizationStatus(status="revoked")
        if session_id and session_id not in self._sessions:
            return AuthorizationStatus(session_id=session_id, status="failed", detail="unknown_session")
        if session_id:
            sess = self._sessions[session_id]
            if sess.user_id != self.owner or (state_nonce is not None and not secrets.compare_digest(state_nonce, sess.state_nonce)):
                raise PermissionError("SESSION_DENIED")
            if sess.status in {"cancelled", "failed", "expired"}:
                return AuthorizationStatus(session_id=session_id, status=sess.status)
            if not active_until(sess.expires_at):
                sess.status = "expired"
                return AuthorizationStatus(session_id=session_id, status="expired")
            polled = await self._source.poll_authorization(session_id)
            if polled.status in ("authorized", "valid"):
                sess.status = "authorized"
                sess.authorized_subject_id = polled.subject_id
                # Persist cookie bytes in credential store (never log). Also keep a ref index.
                key = f"cred:{sess.user_id}:{sess.provider}"
                previous = self._host.local_storage.read_json(f"auth/credential_index_{sess.user_id}.json") or {}
                if previous.get("subject_id") and previous["subject_id"] != polled.subject_id:
                    consent = self._host.local_storage.read_json(f"config/consent_{self.owner}.json")
                    if consent:
                        self._host.local_storage.write_json(f"config/consent_{self.owner}.json", {**consent, "status": "revoked"})
                    audit(self._host, "account_changed_requires_consent", owner=self.owner)
                export = getattr(self._source, "export_cookie", None)
                cookie = export() if callable(export) else ""
                if cookie:
                    await self._host.credential_store.save(key, cookie.encode("utf-8"))
                    self._host.local_storage.write_json(
                        f"auth/credential_index_{sess.user_id}.json",
                        {
                            "provider": sess.provider,
                            "subject_id": polled.subject_id,
                            "session_id": session_id,
                            "updated_at": self._host.clock.now_iso(),
                            "has_cookie": True,
                        },
                    )
                else:
                    await self._host.credential_store.save(
                        key, f"ref:{session_id}:{polled.subject_id or ''}".encode("utf-8")
                    )
                self._host.local_storage.write_json(
                    f"auth/sessions/{session_id}.json",
                    sess.model_dump(mode="json"),
                )
                self._host.local_storage.write_json(f"auth/revoked_{self.owner}.json", {"revoked": False})
                audit(self._host, "authorization_completed", owner=self.owner, session_id=session_id)
            return polled
        return await self._source.check_authorization()

    async def restore_credentials(self, owner_user_id: str, *, provider: str = "netease") -> bool:
        """Load cookie from Memory Bank credential store into the source adapter."""
        key = f"cred:{owner_user_id}:{provider}"
        if owner_user_id != self.owner or (self._host.local_storage.read_json(f"auth/revoked_{self.owner}.json") or {}).get("revoked"):
            return False
        raw = await self._host.credential_store.load(key)
        if not raw:
            return False
        text = raw.decode("utf-8", errors="replace")
        if text.startswith("ref:"):
            return False
        importer = getattr(self._source, "import_cookie", None)
        if not callable(importer):
            return False
        meta = self._host.local_storage.read_json(f"auth/credential_index_{owner_user_id}.json") or {}
        importer(text, subject_id=str(meta.get("subject_id") or "") or None)
        return True

    async def revoke(self, subject_id: str) -> None:
        binding = self._host.local_storage.read_json(f"auth/credential_index_{self.owner}.json") or {}
        known = binding.get("subject_id") or getattr(self._source, "subject_id", None) or getattr(self._source, "_subject_id", None)
        if known and subject_id != known:
            raise PermissionError("SUBJECT_DENIED")
        self._host.local_storage.write_json(f"auth/revoked_{self.owner}.json", {"revoked": True, "at": self._host.clock.now_iso()})
        await self._host.credential_store.delete(f"cred:{self.owner}:netease")
        self._host.local_storage.write_json(f"auth/credential_index_{self.owner}.json", {"status": "revoked"})
        for sess in self._sessions.values():
            sess.status = "cancelled"
            self._host.local_storage.write_json(f"auth/sessions/{sess.session_id}.json", sess.model_dump(mode="json"))
        await self._source.revoke_authorization()
        audit(self._host, "authorization_revoked", owner=self.owner)
        self._host.event_publisher.publish(
            "music.auth.revoked",
            {"subject_id": subject_id, "at": self._host.clock.now_iso()},
        )
        self._host.logger.info("auth_revoked")

    def cancel(self, session_id, nonce):
        sess = self._sessions.get(session_id)
        if not sess or not secrets.compare_digest(nonce, sess.state_nonce):
            raise PermissionError("SESSION_DENIED")
        sess.status = "cancelled"
        self._host.local_storage.write_json(f"auth/sessions/{session_id}.json", sess.model_dump(mode="json"))
        return {"ok": True, "status": "cancelled"}
