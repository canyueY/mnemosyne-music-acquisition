# -*- coding: utf-8 -*-
"""Auth Manager — QR session lifecycle; credentials never logged."""

from __future__ import annotations

import secrets

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.contracts.source_adapter import MusicSourceAdapter
from music_acquisition.domain.models import (
    AuthorizationChallenge,
    AuthorizationRequest,
    AuthorizationSession,
    AuthorizationStatus,
)


class AuthManager:
    def __init__(self, host: HostRuntime, source: MusicSourceAdapter) -> None:
        self._host = host
        self._source = source
        self._sessions: dict[str, AuthorizationSession] = {}

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationChallenge:
        challenge = await self._source.begin_authorization()
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
        return challenge

    async def get_status(self, session_id: str | None = None) -> AuthorizationStatus:
        if session_id and session_id in self._sessions:
            polled = await self._source.poll_authorization(session_id)
            sess = self._sessions[session_id]
            if polled.status in ("authorized", "valid"):
                sess.status = "authorized"
                sess.authorized_subject_id = polled.subject_id
                # Persist cookie bytes in credential store (never log). Also keep a ref index.
                key = f"cred:{sess.user_id}:{sess.provider}"
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
            return polled
        return await self._source.check_authorization()

    async def restore_credentials(self, owner_user_id: str, *, provider: str = "netease") -> bool:
        """Load cookie from Memory Bank credential store into the source adapter."""
        key = f"cred:{owner_user_id}:{provider}"
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
        await self._source.revoke_authorization()
        # best-effort wipe credentials for subject
        # keys are owner-scoped; wipe all netease creds in store is host-specific
        self._host.event_publisher.publish(
            "music.auth.revoked",
            {"subject_id": subject_id, "at": self._host.clock.now_iso()},
        )
        self._host.logger.info("auth_revoked", subject_id=subject_id)
