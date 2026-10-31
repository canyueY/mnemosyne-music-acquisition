# -*- coding: utf-8 -*-
"""Cross-platform exclusive file lock via O_EXCL sentinel."""

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
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return
            except FileExistsError:
                # stale lock: older than 10 minutes → break
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > 600:
                        self.path.unlink(missing_ok=True)  # type: ignore[call-arg]
                        continue
                except Exception:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"file_lock_timeout:{self.path}")
                time.sleep(self.poll_sec)

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None
        try:
            self.path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            # py3.7 compat
            if self.path.is_file():
                self.path.unlink()
        except Exception:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()
