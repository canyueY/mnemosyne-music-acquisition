# -*- coding: utf-8 -*-
"""OS-held locks, released after process death; never steal a live lock by age."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


class FileLock:
    def __init__(self, path: Path | str, *, timeout_sec: float = 30.0, poll_sec: float = 0.05) -> None:
        self.path = Path(path)
        self.timeout_sec = float(timeout_sec)
        self.poll_sec = float(poll_sec)
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                if self._fd is None:
                    self._fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
                    if os.fstat(self._fd).st_size == 0:
                        os.write(self._fd, b"0")
                os.lseek(self._fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    if self._fd is not None:
                        os.close(self._fd)
                        self._fd = None
                    raise TimeoutError("lock_busy")
                time.sleep(self.poll_sec)

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.lseek(self._fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()
