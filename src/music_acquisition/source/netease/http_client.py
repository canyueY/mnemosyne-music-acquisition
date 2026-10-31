# -*- coding: utf-8 -*-
"""Standalone NetEase web API client (no Murasame / no ncm-cli required)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from music_acquisition.source.http_error import SourceHttpError

PLAYLIST_DETAIL = "https://music.163.com/api/v6/playlist/detail"
SONG_DETAIL = "https://music.163.com/api/v3/song/detail"
DAILY_RECOMMEND = "https://music.163.com/api/v3/discovery/recommend/songs"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
    "Cookie": "os=pc; appver=2.9.8;",
}


def _get_json(url: str, params: dict[str, str] | None = None, timeout: float = 25.0, *, cookie="") -> Any:
    q = urllib.parse.urlencode(params or {})
    full = f"{url}?{q}" if q else url
    req = urllib.request.Request(full, headers={k: v for k, v in _HEADERS.items() if k != "Cookie"})
    req.add_unredirected_header("Cookie", cookie or _HEADERS["Cookie"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise PermissionError("LOGIN_REQUIRED") from exc
        raise SourceHttpError(exc.code, exc.headers.get("Retry-After") if exc.headers else None) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError("NETWORK_ERROR") from exc


def parse_track(raw: dict[str, Any]) -> dict[str, Any] | None:
    try:
        sid = int(raw.get("id") or 0)
    except (TypeError, ValueError):
        return None
    if sid <= 0:
        return None
    name = str(raw.get("name") or "")
    artists = []
    for a in raw.get("ar") or raw.get("artists") or []:
        if isinstance(a, dict) and a.get("name"):
            artists.append(
                {
                    "provider_artist_id": str(a.get("id") or ""),
                    "name": str(a.get("name") or ""),
                }
            )
    al = raw.get("al") or raw.get("album") or {}
    album = None
    if isinstance(al, dict) and (al.get("name") or al.get("id")):
        album = {
            "provider_album_id": str(al.get("id") or ""),
            "name": str(al.get("name") or ""),
        }
    duration = raw.get("dt") or raw.get("duration")
    try:
        duration_ms = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    return {
        "track_id": f"netease:{sid}",
        "provider": "netease",
        "provider_track_id": str(sid),
        "title": name,
        "artists": artists,
        "album": album,
        "duration_ms": duration_ms,
        "metadata": {"source": "netease_web", "schema_version": "music.canonical.v1"},
        "extensions": raw,
    }


def extract_track_ids(playlist: dict[str, Any]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for raw in playlist.get("trackIds") or []:
        v = raw.get("id") if isinstance(raw, dict) else raw
        try:
            sid = int(v)
        except (TypeError, ValueError):
            continue
        if sid > 0 and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def fetch_songs_by_ids(song_ids: list[int], *, cookie="") -> list[dict[str, Any]]:
    ids = [int(x) for x in song_ids if int(x) > 0]
    if not ids:
        return []
    payload = json.dumps([{"id": sid} for sid in ids], separators=(",", ":"))
    data = _get_json(SONG_DETAIL, {"c": payload}, cookie=cookie)
    songs = (data.get("songs") or []) if isinstance(data, dict) else []
    out: list[dict[str, Any]] = []
    for raw in songs:
        if isinstance(raw, dict):
            t = parse_track(raw)
            if t:
                out.append(t)
    return out


def fetch_playlist_tracks(playlist_id: int, *, limit: int | None = None, cookie="", page_cache=None) -> tuple[list[dict[str, Any]], int, bool]:
    """Returns (tracks, expected_count, complete)."""
    pid = int(playlist_id)
    lim = max(1, int(limit)) if limit is not None else 1000000
    data = _get_json(PLAYLIST_DETAIL, {"id": str(pid), "n": str(min(lim, 500))}, cookie=cookie)
    playlist = data.get("playlist") if isinstance(data, dict) else None
    if not isinstance(playlist, dict):
        raise RuntimeError(f"playlist_unavailable:{pid}")
    expected = int(playlist["trackCount"]) if playlist.get("trackCount") is not None else None
    tracks_raw = playlist.get("tracks") or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in tracks_raw:
        if not isinstance(raw, dict):
            continue
        t = parse_track(raw)
        if not t or t["provider_track_id"] in seen:
            continue
        seen.add(t["provider_track_id"])
        out.append(t)
        if len(out) >= lim:
            break
    if len(out) < lim:
        need = [sid for sid in extract_track_ids(playlist) if str(sid) not in seen]
        need = need[: max(0, lim - len(out))]
        partial_fail = False
        for i in range(0, len(need), 200):
            batch = need[i : i + 200]
            try:
                import hashlib
                key = hashlib.sha256(json.dumps([pid, batch, playlist.get("updateTime")]).encode()).hexdigest()
                import time
                cached = page_cache.read_json(f"pages/{key}.json") if page_cache else None
                fetched = cached.get("records") if isinstance(cached, dict) and time.time() - cached.get("cached_epoch", 0) < 86400 else None
                if fetched is None:
                    for attempt in range(3):
                        try:
                            fetched = fetch_songs_by_ids(batch, cookie=cookie)
                            break
                        except PermissionError:
                            raise
                        except (OSError, RuntimeError) as exc:
                            if isinstance(exc, SourceHttpError) and exc.retry_after:
                                raise
                            if attempt == 2:
                                raise
                            from music_acquisition.sync.retry import should_retry, backoff_delay
                            if not isinstance(exc, OSError) and not should_retry(str(exc)):
                                raise
                            time.sleep(backoff_delay(attempt, base=.1, max_delay=2))
                    if page_cache and {str(x["provider_track_id"]) for x in fetched} == {str(x) for x in batch}:
                        from datetime import datetime, timezone
                        page_cache.write_json(f"pages/{key}.json", {"records": fetched, "cached_epoch": time.time(), "at": datetime.now(timezone.utc).isoformat()})
            except PermissionError:
                raise
            except SourceHttpError:
                raise
            except (ConnectionError, TimeoutError):
                raise
            except Exception:
                partial_fail = True
                continue
            for t in fetched:
                if t["provider_track_id"] in seen:
                    continue
                seen.add(t["provider_track_id"])
                out.append(t)
                if len(out) >= lim:
                    break
            if len(out) >= lim:
                break
        if expected is not None and len(out) != expected:
            partial_fail = True
        complete = not partial_fail and expected is not None and len(out) == expected
    else:
        complete = expected is not None and len(out) == expected
    order = {str(tid): i for i, tid in enumerate(extract_track_ids(playlist))}
    out.sort(key=lambda row: order.get(row["provider_track_id"], len(order)))
    return out[:lim], int(expected if expected is not None else len(out)), complete


def fetch_daily_songs(*, limit: int = 30) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 30))
    data = _get_json(DAILY_RECOMMEND)
    block = data.get("data") if isinstance(data, dict) else None
    songs = (block or {}).get("dailySongs") if isinstance(block, dict) else None
    if not isinstance(songs, list):
        raise RuntimeError("daily_recommend_bad_payload")
    out: list[dict[str, Any]] = []
    for raw in songs:
        if isinstance(raw, dict):
            t = parse_track(raw)
            if t:
                out.append(t)
        if len(out) >= lim:
            break
    if not out:
        raise RuntimeError("daily_recommend_empty")
    return out
