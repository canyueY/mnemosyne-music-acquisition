# -*- coding: utf-8 -*-
"""Encrypt NetEase Cookie blobs at rest (Fernet + OS keyring or local master key).

Cookie bytes must never be logged. On disk they are stored as::

    ENC1:<fernet-token>

Legacy plaintext ``.bin`` files are auto-upgraded on next ``save``/``load``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path
from typing import Any

_PREFIX = b"ENC1:"
_KEYRING_SERVICE = "mnemosyne-music-acquisition"
_KEYRING_USER = "credential-master-v1"


def is_encrypted_blob(data: bytes | None) -> bool:
    return bool(data) and data.startswith(_PREFIX)


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet


def _load_or_create_master_key(key_dir: Path) -> bytes:
    """Return a 32-byte url-safe Fernet key.

    Prefer OS keyring; fall back to ``<key_dir>/.master.key`` (0600 best-effort).
    """
    Fernet = _fernet()
    # 1) keyring
    try:
        import keyring  # type: ignore

        existing = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if existing:
            return existing.encode("ascii")
        raw = Fernet.generate_key()
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, raw.decode("ascii"))
        return raw
    except Exception:
        pass

    # 2) local master key file
    key_dir.mkdir(parents=True, exist_ok=True)
    path = key_dir / ".master.key"
    if path.is_file():
        return path.read_bytes().strip()
    raw = Fernet.generate_key()
    path.write_bytes(raw)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    # sidecar note (no secrets)
    note = key_dir / "README_SECURITY.txt"
    if not note.is_file():
        note.write_text(
            "Credentials are Fernet-encrypted (ENC1:...). "
            "Master key lives in OS keyring when available, else .master.key. "
            "Do not commit or share this directory.\n",
            encoding="utf-8",
        )
    return raw


def encrypt_blob(plaintext: bytes, *, key_dir: Path | str) -> bytes:
    Fernet = _fernet()
    key = _load_or_create_master_key(Path(key_dir))
    token = Fernet(key).encrypt(plaintext)
    return _PREFIX + token


def decrypt_blob(blob: bytes, *, key_dir: Path | str) -> bytes:
    if not is_encrypted_blob(blob):
        return blob
    Fernet = _fernet()
    key = _load_or_create_master_key(Path(key_dir))
    return Fernet(key).decrypt(blob[len(_PREFIX) :])


class CredentialCrypto:
    """Stateful helper bound to a credentials directory."""

    def __init__(self, credentials_root: Path | str) -> None:
        self.root = Path(credentials_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def seal(self, plaintext: bytes) -> bytes:
        return encrypt_blob(plaintext, key_dir=self.root)

    def open(self, blob: bytes) -> bytes:
        return decrypt_blob(blob, key_dir=self.root)

    def fingerprint(self, plaintext: bytes) -> str:
        """Non-reversible short hash for diagnostics (never log raw cookie)."""
        return hashlib.sha256(plaintext).hexdigest()[:12]

    def status(self) -> dict[str, Any]:
        keyring_ok = False
        try:
            import keyring  # type: ignore

            keyring_ok = bool(keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER))
        except Exception:
            keyring_ok = False
        master = self.root / ".master.key"
        return {
            "encryption": "fernet_enc1",
            "keyring": keyring_ok,
            "local_master_key": master.is_file(),
            "prefix": "ENC1:",
        }
