# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_record(value):
    if isinstance(value, list):
        return [content_record(v) for v in value]
    if isinstance(value, dict):
        result = {k: content_record(v) for k, v in value.items()}
        if isinstance(result.get("metadata"), dict):
            result["metadata"] = {k: v for k, v in result["metadata"].items() if k not in {"fetched_at", "fetchedAt", "captured_at", "imported_at", "request_id"}}
        return result
    return value


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
    return hashlib.sha256(canonical_json(content_record(payload)).encode("utf-8")).hexdigest()
