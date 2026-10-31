# -*- coding: utf-8 -*-
from __future__ import annotations

import random


def backoff_delay(retry_count: int, *, base: float = 1.0, max_delay: float = 60.0) -> float:
    delay = min(base * (2 ** max(0, retry_count)), max_delay)
    jitter = random.uniform(0, delay * 0.2)
    return delay + jitter


def should_retry(error_code: str | None) -> bool:
    if not error_code:
        return False
    code = error_code.upper()
    if any(x in code for x in ("LOGIN", "AUTH", "SCOPE", "UNSUPPORTED", "CANCEL")):
        return False
    if any(x in code for x in ("NETWORK", "TIMEOUT", "HTTP_5", "HTTP_429", "PARTIAL")):
        return True
    return False
