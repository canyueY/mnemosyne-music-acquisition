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
    """Prefer an existing key; installing an OS backend must not change the key."""
    from music_acquisition.util.runtime import atomic_write
    from music_acquisition.util.file_lock import FileLock
    secured = key_dir.parent / "credential-keys"
    secured.mkdir(parents=True, exist_ok=True)
    with FileLock(secured / ".key.lock"):
        wrapped, local = secured / "master.dpapi", secured / ".master.key"
        legacy = key_dir / ".master.key"
        if os.name == "nt" and wrapped.is_file():
            return _dpapi(wrapped.read_bytes(), decrypt=True)
        raw = local.read_bytes().strip() if local.is_file() else legacy.read_bytes().strip() if legacy.is_file() else None
        if raw is None:
            try:
                import keyring
                # Read the old global vault entry only when migrating existing data.
                if any(key_dir.glob("*.bin")):
                    previous = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
                    raw = previous.encode("ascii") if previous else None
                if os.name != "nt" and raw is None:
                    account = hashlib.sha256(str(secured.resolve()).encode()).hexdigest()
                    previous = keyring.get_password(_KEYRING_SERVICE, account)
                    raw = previous.encode("ascii") if previous else _fernet().generate_key()
                    if not previous:
                        keyring.set_password(_KEYRING_SERVICE, account, raw.decode("ascii"))
                    return raw
            except Exception:
                pass
        raw = raw or _fernet().generate_key()
        if os.name == "nt":
            atomic_write(wrapped, _dpapi(raw))
            if legacy.is_file(): legacy.unlink()
            if local.is_file(): local.unlink()
        else:
            atomic_write(local, raw)
            os.chmod(local, 0o600)
        return raw


def _dpapi(value: bytes, *, decrypt=False):
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buf = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, result = Blob(len(value), buf), Blob()
    library = ctypes.windll.crypt32
    if decrypt:
        ok = library.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = library.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise OSError("OS_CREDENTIAL_PROTECTION_FAILED")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(result.data)


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
