# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_hash(records: list[Any]) -> str:
    """SHA256 of canonical JSON of records (content-stable)."""
    payload = []
    for r in records:
        if hasattr(r, "model_dump"):
            payload.append(r.model_dump(mode="json"))
        elif isinstance(r, dict):
            payload.append(r)
        else:
            payload.append(r)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
