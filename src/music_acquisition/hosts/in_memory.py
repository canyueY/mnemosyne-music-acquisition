# -*- coding: utf-8 -*-
"""In-memory HostRuntime for contract tests (no DSH / no real FS required)."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class _MemCredentials:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def save(self, key: str, value: bytes) -> None:
        self._data[key] = value

    async def load(self, key: str) -> bytes | None:
        return self._data.get(key)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self._data


class _MemScheduler:
    def __init__(self) -> None:
        self._jobs: dict[str, Callable[..., Any]] = {}

    def schedule(self, job_id: str, delay_sec: float, callback: Any) -> str:
        self._jobs[job_id] = callback
        return job_id

    def cancel(self, handle: str) -> None:
        self._jobs.pop(handle, None)


class _MemEvents:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))


class _MemStorage:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(tempfile.mkdtemp(prefix="music-acq-"))
        self._root.mkdir(parents=True, exist_ok=True)

    def root(self) -> str:
        return str(self._root)

    def path(self, *parts: str) -> str:
        p = self._root.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    def write_bytes(self, rel: str, data: bytes) -> None:
        Path(self.path(rel)).write_bytes(data)

    def read_bytes(self, rel: str) -> bytes | None:
        p = self._root / rel
        return p.read_bytes() if p.is_file() else None

    def write_json(self, rel: str, obj: Any) -> None:
        self.write_bytes(rel, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))

    def read_json(self, rel: str) -> Any | None:
        raw = self.read_bytes(rel)
        return json.loads(raw.decode("utf-8")) if raw else None


class _MemHttp:
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_sec: float = 30.0,
    ) -> tuple[int, bytes, dict[str, str]]:
        return 501, b'{"error":"http_not_wired"}', {}


class _MemUI:
    def __init__(self) -> None:
        self.qr_shown: list[dict[str, str]] = []
        self.notes: list[tuple[str, str]] = []

    async def show_qr(self, session_id: str, image_or_url: str, message: str = "") -> None:
        self.qr_shown.append({"session_id": session_id, "url": image_or_url, "message": message})

    async def notify(self, level: str, message: str) -> None:
        self.notes.append((level, message))


class _MemLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg: str, **fields: Any) -> None:
        self.lines.append(f"INFO {msg} {fields}")

    def warning(self, msg: str, **fields: Any) -> None:
        self.lines.append(f"WARN {msg} {fields}")

    def error(self, msg: str, **fields: Any) -> None:
        self.lines.append(f"ERROR {msg} {fields}")


class _MemClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def timezone(self) -> str:
        return "Asia/Shanghai"


class InMemoryHostRuntime:
    """Test / standalone-dev host — never imports DSH or Mnemosyne core."""

    def __init__(self, root: Path | None = None) -> None:
        self.credential_store = _MemCredentials()
        self.task_scheduler = _MemScheduler()
        self.event_publisher = _MemEvents()
        self.local_storage = _MemStorage(root)
        self.http_transport = _MemHttp()
        self.user_interaction = _MemUI()
        self.logger = _MemLogger()
        self.clock = _MemClock()
        self.host_name = "in_memory"
