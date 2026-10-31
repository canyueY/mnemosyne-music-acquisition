# -*- coding: utf-8 -*-
"""DSH Host adapter (Mode A) — runnable HostRuntime, not a full npm DSH plugin.

This is the Python side of ``hosts/dsh-mnemosyne-music``. It maps DSH-style
environment variables onto ``FilesystemHostRuntime`` so a DSH plugin (or
manual process) can load the Engine without embedding acquisition logic.

Env:
  DSH_MUSIC_DATA / MNEMOSYNE_MUSIC_DATA — data root
  DSH_MEMORY_DATA_DIR — falls back to Memory Bank / music-acquisition
  MNEMOSYNE_BASE_URL / DSH_MEMORY_TOKEN — Node HTTP
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from music_acquisition.bank_paths import music_acquisition_root
from music_acquisition.hosts.filesystem import FilesystemHostRuntime


def resolve_dsh_data_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    for key in ("DSH_MUSIC_DATA", "MNEMOSYNE_MUSIC_DATA"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return Path(val)
    bank = (os.environ.get("DSH_MEMORY_DATA_DIR") or "").strip()
    if bank:
        return Path(bank) / "music-acquisition"
    return music_acquisition_root(create=True)


class DshHostRuntime(FilesystemHostRuntime):
    """Filesystem host with DSH-oriented defaults and host_name."""

    def __init__(self, root: str | Path | None = None, *, timezone: str = "Asia/Shanghai") -> None:
        base = resolve_dsh_data_root(root)
        base.mkdir(parents=True, exist_ok=True)
        super().__init__(base, timezone=timezone)
        self.host_name = "dsh_host"


def open_dsh_host(data_dir: str | Path | None = None) -> DshHostRuntime:
    return DshHostRuntime(data_dir)


def dsh_runtime_info() -> dict[str, Any]:
    root = resolve_dsh_data_root()
    return {
        "host": "dsh_host",
        "data_root": str(root),
        "mnemosyne_url": os.environ.get("MNEMOSYNE_BASE_URL") or "",
        "has_token": bool(os.environ.get("DSH_MEMORY_TOKEN")),
        "note": (
            "Python HostRuntime ready. npm package hosts/dsh-mnemosyne-music "
            "spawns this runtime; it is not a full Cordis plugin marketplace release."
        ),
    }


# Back-compat alias for older imports
DshHostRuntimeStub = DshHostRuntime

__all__ = [
    "DshHostRuntime",
    "DshHostRuntimeStub",
    "open_dsh_host",
    "resolve_dsh_data_root",
    "dsh_runtime_info",
]
