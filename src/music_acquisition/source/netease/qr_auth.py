# -*- coding: utf-8 -*-
"""NetEase QR login (official web endpoints) — no password to Agent."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from music_acquisition.source.http_error import SourceHttpError

QR_UNIKEY = "https://music.163.com/api/login/qrcode/unikey"
QR_CHECK = "https://music.163.com/api/login/qrcode/client/login"
ACCOUNT = "https://music.163.com/api/nuser/account/get"
DAILY = "https://music.163.com/api/v3/discovery/recommend/songs"
USER_RECORD = "https://music.163.com/api/v1/user/record"

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}


@dataclass
class QrSession:
    unikey: str
    scan_url: str
    status: str = "waiting_scan"  # waiting_scan | confirming | authorized | expired | failed
    cookie: str = ""
    profile: dict[str, Any] = field(default_factory=dict)
    raw_last: dict[str, Any] = field(default_factory=dict)


def _request(
    url: str,
    *,
    params: dict[str, str] | None = None,
    cookie: str = "",
    method: str = "GET",
    timeout: float = 20.0,
) -> tuple[dict[str, Any], str]:
    """Returns (json_body, set_cookie_joined)."""
    q = urllib.parse.urlencode(params or {})
    full = f"{url}?{q}" if q else url
    headers = dict(_BASE_HEADERS)
    base_cookie = "os=pc; appver=2.9.8;"
    credential = f"{base_cookie} {cookie}".strip() if cookie else base_cookie
    req = urllib.request.Request(full, headers=headers, method=method)
    req.add_unredirected_header("Cookie", credential)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
            # http.client headers may be HTTPMessage
            raw_sc = resp.headers.get_all("Set-Cookie") if hasattr(resp.headers, "get_all") else None
            if not raw_sc:
                sc = resp.headers.get("Set-Cookie") or ""
                raw_sc = [sc] if sc else []
            joined = "; ".join(_cookie_pair(c) for c in raw_sc if c)
            return body if isinstance(body, dict) else {"raw": body}, joined
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            raise SourceHttpError(exc.code, exc.headers.get("Retry-After") if exc.headers else None) from exc
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        try:
            body = json.loads(detail)
        except json.JSONDecodeError:
            body = {"code": exc.code, "message": "HTTP_ERROR"}
        return body, ""


def _cookie_pair(set_cookie: str) -> str:
    # keep only name=value
    return set_cookie.split(";", 1)[0].strip()


def create_qr_session() -> QrSession:
    body, _ = _request(QR_UNIKEY, params={"type": "1"})
    if int(body.get("code") or 0) != 200 or not body.get("unikey"):
        raise RuntimeError("QR_UNIKEY_FAILED")
    unikey = str(body["unikey"])
    # NetEase mobile app scans this URL form
    scan_url = f"https://music.163.com/login?codekey={unikey}"
    return QrSession(unikey=unikey, scan_url=scan_url, status="waiting_scan")


def poll_qr(session: QrSession) -> QrSession:
    body, set_cookie = _request(
        QR_CHECK, params={"type": "1", "key": session.unikey}
    )
    session.raw_last = body
    code = int(body.get("code") or 0)
    # 800 expired, 801 waiting, 802 scanned confirming, 803 ok
    if code == 801:
        session.status = "waiting_scan"
    elif code == 802:
        session.status = "confirming"
    elif code == 800:
        session.status = "expired"
    elif code == 803:
        session.status = "authorized"
        cookie_from_body = str(body.get("cookie") or "").strip()
        # body cookie may be full string; merge with Set-Cookie
        parts = []
        if cookie_from_body:
            parts.append(cookie_from_body.replace("\n", "; "))
        if set_cookie:
            parts.append(set_cookie)
        # also extract MUSIC_U if present in message-like fields
        session.cookie = "; ".join(p for p in parts if p)
        if "MUSIC_U" not in session.cookie and cookie_from_body:
            session.cookie = cookie_from_body
        session.profile = fetch_account(session.cookie)
    else:
        session.status = "failed"
    return session


def fetch_account(cookie: str) -> dict[str, Any]:
    body, _ = _request(ACCOUNT, cookie=cookie)
    profile = body.get("profile") if isinstance(body.get("profile"), dict) else {}
    account = body.get("account") if isinstance(body.get("account"), dict) else {}
    return {
        "code": body.get("code"),
        "userId": profile.get("userId") or account.get("id"),
        "nickname": profile.get("nickname") or "",
        "avatarUrl": profile.get("avatarUrl") or "",
        "raw_keys": list(body.keys())[:12],
    }


def fetch_daily_with_cookie(cookie: str, *, limit: int = 30) -> dict[str, Any]:
    body, _ = _request(DAILY, cookie=cookie)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    songs = data.get("dailySongs") if isinstance(data.get("dailySongs"), list) else []
    sample = []
    for s in songs[:limit]:
        if isinstance(s, dict):
            sample.append({"id": s.get("id"), "name": s.get("name")})
    return {
        "code": body.get("code"),
        "count": len(songs),
        "sample": sample[:5],
        "personalized_hint": "compare_ids_vs_anonymous_pool",
    }


def fetch_user_record(cookie: str, uid: int | str, *, kind: int = 1) -> dict[str, Any]:
    """kind: 0=all time, 1=week. Requires login + matching uid permissions."""
    body, _ = _request(
        USER_RECORD,
        params={"uid": str(uid), "type": str(kind)},
        cookie=cookie,
    )
    week = body.get("weekData") if isinstance(body.get("weekData"), list) else []
    all_data = body.get("allData") if isinstance(body.get("allData"), list) else []
    return {
        "code": body.get("code"),
        "msg": body.get("msg") or body.get("message") or "",
        "week_count": len(week),
        "all_count": len(all_data),
        "sample": [
            {
                "id": (x.get("song") or {}).get("id") if isinstance(x, dict) else None,
                "name": (x.get("song") or {}).get("name") if isinstance(x, dict) else None,
                "playCount": x.get("playCount") if isinstance(x, dict) else None,
            }
            for x in (week or all_data)[:5]
            if isinstance(x, dict)
        ],
    }


def save_qr_png(scan_url: str, path: str) -> str:
    import qrcode

    img = qrcode.make(scan_url)
    img.save(path)
    return path
