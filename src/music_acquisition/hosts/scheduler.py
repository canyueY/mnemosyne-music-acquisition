# -*- coding: utf-8 -*-
"""Real in-process TaskScheduler (threading timers + optional interval jobs)."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable


class ThreadingScheduler:
    """HostRuntime.task_scheduler implementation that actually fires callbacks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._timers: dict[str, threading.Timer] = {}
        self._interval: dict[str, dict[str, Any]] = {}
        self._stopped = False

    def schedule(self, job_id: str, delay_sec: float, callback: Any) -> str:
        handle = str(job_id or f"job_{uuid.uuid4().hex[:10]}")
        with self._lock:
            self.cancel(handle)

            def _run() -> None:
                try:
                    callback()
                finally:
                    with self._lock:
                        self._timers.pop(handle, None)

            timer = threading.Timer(max(0.0, float(delay_sec)), _run)
            timer.daemon = True
            self._timers[handle] = timer
            timer.start()
        return handle

    def schedule_every(
        self,
        job_id: str,
        interval_sec: float,
        callback: Callable[[], Any],
        *,
        initial_delay_sec: float | None = None,
    ) -> str:
        """Recurring job. Returns handle (== job_id)."""
        handle = str(job_id or f"every_{uuid.uuid4().hex[:10]}")
        interval = max(1.0, float(interval_sec))
        delay = interval if initial_delay_sec is None else max(0.0, float(initial_delay_sec))

        def _tick() -> None:
            if self._stopped:
                return
            try:
                callback()
            except Exception:
                pass
            # reschedule
            with self._lock:
                meta = self._interval.get(handle)
                if not meta or self._stopped:
                    return
            self.schedule(handle, interval, _tick)

        with self._lock:
            self._interval[handle] = {
                "interval_sec": interval,
                "created_at": time.time(),
            }
        self.schedule(handle, delay, _tick)
        return handle

    def cancel(self, handle: str) -> None:
        with self._lock:
            t = self._timers.pop(handle, None)
            self._interval.pop(handle, None)
            if t is not None:
                try:
                    t.cancel()
                except Exception:
                    pass

    def shutdown(self) -> None:
        self._stopped = True
        with self._lock:
            handles = list(self._timers.keys()) + list(self._interval.keys())
        for h in handles:
            self.cancel(h)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "pending_timers": list(self._timers.keys()),
                "interval_jobs": {k: dict(v) for k, v in self._interval.items()},
                "stopped": self._stopped,
            }
