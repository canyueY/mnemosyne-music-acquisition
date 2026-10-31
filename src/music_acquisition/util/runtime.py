"""Small deterministic boundaries shared by storage, authorization and sync."""
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed


def active_until(value, *, now=None):
    if not value:
        return True
    try:
        return parse_time(value) > (now or datetime.now(timezone.utc))
    except (ValueError, TypeError, AttributeError):
        return False


def local_date(clock):
    return parse_time(clock.now_iso()).astimezone(ZoneInfo(clock.timezone())).date().isoformat()


def under(root, *parts):
    base = Path(root).resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise ValueError("path_outside_data_root")
    return target


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def audit(host, event, **fields):
    # Callers supply identifiers/counts/error codes, never raw responses or secrets.
    import secrets
    host.local_storage.write_json(f"audit/{secrets.token_hex(12)}.json", {
        "event": event, "at": host.clock.now_iso(), **fields})
