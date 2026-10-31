# -*- coding: utf-8 -*-
"""Resolve Mnemosyne Memory Bank paths for music-acquisition (Mode B).

代码仓（mnemosyne / mnemosyne-music-acquisition）≠ Memory Bank。
组件运行时数据落在 Bank 下的 ``music-acquisition/``，不污染 memory.db。
"""

from __future__ import annotations

import os
from pathlib import Path

MEMORY_BANK_NAME = "Mnemosyne Memory Bank"
MUSIC_ACQUISITION_DIRNAME = "music-acquisition"
LEGACY_DSH_SEGMENT = ("DSH", "cognitive-memory")


def memory_bank_root() -> Path:
    """权威记忆库根目录（与 ``cognitive_memory.storage.paths.default_data_root`` 对齐）。"""
    for key in ("MNEMOSYNE_MEMORY_BANK", "MNEMOSYNE_DATA_DIR", "DSH_MEMORY_DATA_DIR"):
        override = str(os.environ.get(key) or "").strip()
        if override:
            return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if local:
        return (Path(local) / MEMORY_BANK_NAME).resolve()
    return (Path.home() / ".local" / "share" / MEMORY_BANK_NAME).resolve()


def music_acquisition_root(*, create: bool = True) -> Path:
    """``<Memory Bank>/music-acquisition`` — Engine HostRuntime 数据根。

    可用 ``MNEMOSYNE_MUSIC_DATA`` 覆盖（测试 / 多实例）。
    """
    override = str(os.environ.get("MNEMOSYNE_MUSIC_DATA") or "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = memory_bank_root() / MUSIC_ACQUISITION_DIRNAME
    if create:
        root.mkdir(parents=True, exist_ok=True)
        for sub in (
            "config",
            "snapshots",
            "outbox",
            "playlist_scopes",
            "credentials",
            "logs",
            "memory_bank_mirror",
            "auth",
            "sync",
        ):
            (root / sub).mkdir(parents=True, exist_ok=True)
        marker = root / "README.txt"
        if not marker.is_file():
            marker.write_text(
                (
                    "Mnemosyne Music Acquisition runtime (Memory Bank component)\n"
                    "==========================================================\n"
                    "This folder lives under Mnemosyne Memory Bank.\n"
                    "It stores snapshots / outbox / playlist scope boundaries.\n"
                    "Do not treat repo working trees as the authority store.\n"
                ),
                encoding="utf-8",
            )
    return root


def playlist_scopes_dir(*, create: bool = True) -> Path:
    root = music_acquisition_root(create=create) / "playlist_scopes"
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def bank_layout_status() -> dict:
    bank = memory_bank_root()
    music = music_acquisition_root(create=False)
    return {
        "bank_name": MEMORY_BANK_NAME,
        "bank_root": str(bank),
        "bank_exists": bank.is_dir(),
        "music_root": str(music),
        "music_exists": music.is_dir(),
        "playlist_scopes": str(music / "playlist_scopes"),
        "env": {
            "MNEMOSYNE_MEMORY_BANK": bool(os.environ.get("MNEMOSYNE_MEMORY_BANK")),
            "MNEMOSYNE_DATA_DIR": bool(os.environ.get("MNEMOSYNE_DATA_DIR")),
            "MNEMOSYNE_MUSIC_DATA": bool(os.environ.get("MNEMOSYNE_MUSIC_DATA")),
        },
    }
