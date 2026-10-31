# -*- coding: utf-8 -*-
"""HostRuntime — sole bridge from Engine to any host (DSH / Mnemosyne Node)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CredentialStore(Protocol):
    async def save(self, key: str, value: bytes) -> None: ...

    async def load(self, key: str) -> bytes | None: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


@runtime_checkable
class TaskScheduler(Protocol):
    def schedule(self, job_id: str, delay_sec: float, callback: Any) -> str: ...

    def cancel(self, handle: str) -> None: ...


@runtime_checkable
class EventPublisher(Protocol):
    def publish(self, topic: str, payload: dict[str, Any]) -> None: ...


@runtime_checkable
class LocalStorage(Protocol):
    def root(self) -> str: ...

    def path(self, *parts: str) -> str: ...

    def write_bytes(self, rel: str, data: bytes) -> None: ...

    def read_bytes(self, rel: str) -> bytes | None: ...

    def write_json(self, rel: str, obj: Any) -> None: ...

    def read_json(self, rel: str) -> Any | None: ...


@runtime_checkable
class HttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_sec: float = 30.0,
    ) -> tuple[int, bytes, dict[str, str]]: ...


@runtime_checkable
class UserInteraction(Protocol):
    async def show_qr(self, session_id: str, image_or_url: str, message: str = "") -> None: ...

    async def notify(self, level: str, message: str) -> None: ...


@runtime_checkable
class Logger(Protocol):
    def info(self, msg: str, **fields: Any) -> None: ...

    def warning(self, msg: str, **fields: Any) -> None: ...

    def error(self, msg: str, **fields: Any) -> None: ...


@runtime_checkable
class Clock(Protocol):
    def now_iso(self) -> str: ...

    def timezone(self) -> str: ...


@runtime_checkable
class HostRuntime(Protocol):
    """Engine must only talk to the host through this contract."""

    credential_store: CredentialStore
    task_scheduler: TaskScheduler
    event_publisher: EventPublisher
    local_storage: LocalStorage
    http_transport: HttpTransport
    user_interaction: UserInteraction
    logger: Logger
    clock: Clock
