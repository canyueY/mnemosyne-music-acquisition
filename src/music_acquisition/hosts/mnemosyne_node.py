# -*- coding: utf-8 -*-
"""Mnemosyne Node host (Mode B) — Memory Bank backed FilesystemHostRuntime.

数据根：``<Mnemosyne Memory Bank>/music-acquisition/``
不 import cognitive_memory 内部实现；路径解析与桌宠 bridge / Node paths 对齐。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from music_acquisition.bank_paths import bank_layout_status, memory_bank_root, music_acquisition_root
from music_acquisition.hosts.filesystem import FilesystemHostRuntime


class MnemosyneNodeHostRuntime(FilesystemHostRuntime):
    """Mode B：绑定到用户的 Mnemosyne Memory Bank。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        timezone: str = "Asia/Shanghai",
        ensure_bank_layout: bool = True,
    ) -> None:
        if ensure_bank_layout:
            bank = memory_bank_root()
            for sub in ("runtime", "data", "logs", "cache", "components"):
                (bank / sub).mkdir(parents=True, exist_ok=True)
        data_root = Path(root) if root else music_acquisition_root(create=True)
        super().__init__(data_root, timezone=timezone)
        self.host_name = "mnemosyne_memory_bank"
        self.bank_root = str(memory_bank_root())
        self.music_root = str(data_root)

    def bank_status(self) -> dict[str, Any]:
        st = bank_layout_status()
        st["host_name"] = self.host_name
        st["music_root"] = self.music_root
        st["bank_root"] = self.bank_root
        return st


def open_mnemosyne_host(
    *,
    data_dir: str | Path | None = None,
    timezone: str = "Asia/Shanghai",
) -> MnemosyneNodeHostRuntime:
    """工厂：无参时默认挂到 Memory Bank。"""
    return MnemosyneNodeHostRuntime(data_dir, timezone=timezone)


__all__ = ["MnemosyneNodeHostRuntime", "open_mnemosyne_host"]
