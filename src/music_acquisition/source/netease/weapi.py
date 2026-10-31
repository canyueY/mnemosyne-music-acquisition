# -*- coding: utf-8 -*-
"""NetEase weapi encrypt helpers (stdlib + cryptography).

Used for endpoints that reject plain ``/api/…`` (e.g. play record).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from typing import Any

# Official web weapi constants (public; same as open NeteaseCloudMusicApi)
_MODULUS = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7"
    "b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280"
    "104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932"
    "575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b"
    "3ece0462db0a22b8e7"
)
_PUBKEY = "010001"
_NONCE = b"0CoJUm6Qyw8W8jud"
_IV = b"0102030405060708"
_WEAPI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad] * pad)


def _aes_cbc_encrypt(plaintext: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.CBC(_IV), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(_pad(plaintext)) + encryptor.finalize()


def _rsa_encrypt(message: str) -> str:
    # NetEase: reverse message, then RSA with no padding (raw modular exponentiation)
    text = message[::-1].encode("utf-8")
    m = int.from_bytes(text, "big")
    e = int(_PUBKEY, 16)
    n = int(_MODULUS, 16)
    c = pow(m, e, n)
    return format(c, "x").zfill(256)


def weapi_encrypt(payload: dict[str, Any]) -> dict[str, str]:
    """Return form fields ``params`` + ``encSecKey`` for weapi POST."""
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    secret = base64.b64encode(os.urandom(16))[:16]
    # first AES with fixed nonce, then AES with random secret
    params = base64.b64encode(_aes_cbc_encrypt(text, _NONCE)).decode("ascii")
    params = base64.b64encode(_aes_cbc_encrypt(params.encode("utf-8"), secret)).decode("ascii")
    enc_sec = _rsa_encrypt(secret.decode("latin-1"))
    return {"params": params, "encSecKey": enc_sec}


def weapi_post(
    path: str,
    payload: dict[str, Any],
    *,
    cookie: str = "",
    timeout: float = 20.0,
) -> dict[str, Any]:
    """POST ``https://music.163.com/weapi/...`` with encrypted body."""
    path = path if path.startswith("/") else f"/{path}"
    if path.startswith("/weapi/"):
        url = f"https://music.163.com{path}"
    elif path.startswith("/api/"):
        url = f"https://music.163.com/weapi{path[4:]}"
    else:
        url = f"https://music.163.com/weapi{path}"
    form = urllib.parse.urlencode(weapi_encrypt(payload)).encode("utf-8")
    headers = {
        "User-Agent": _WEAPI_UA,
        "Referer": "https://music.163.com/",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": f"os=pc; appver=2.9.8; {cookie}".strip(),
    }
    req = urllib.request.Request(url, data=form, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            body = json.loads(raw) if raw else {}
            return body if isinstance(body, dict) else {"raw": body}
    except Exception as exc:  # noqa: BLE001
        return {"code": -1, "error": str(exc)}


def fetch_play_record(
    uid: str | int,
    *,
    cookie: str,
    kind: int = 1,
) -> dict[str, Any]:
    """kind: 1=week, 0=all-time. Returns normalized records list + raw meta."""
    body = weapi_post(
        "/weapi/v1/play/record",
        {"uid": str(uid), "type": str(kind)},
        cookie=cookie,
    )
    code = int(body.get("code") or 0)
    week = body.get("weekData") if isinstance(body.get("weekData"), list) else []
    all_data = body.get("allData") if isinstance(body.get("allData"), list) else []
    rows = week if kind == 1 else (all_data or week)
    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        song = row.get("song") if isinstance(row.get("song"), dict) else {}
        tid = str(song.get("id") or "")
        if not tid:
            continue
        artists = song.get("ar") or song.get("artists") or []
        if isinstance(artists, list):
            names = "/".join(
                str(a.get("name") or "") for a in artists if isinstance(a, dict) and a.get("name")
            )
        else:
            names = str(artists or "")
        records.append(
            {
                "provider_track_id": tid,
                "track_id": tid,
                "title": str(song.get("name") or ""),
                "artists": names,
                "play_count": row.get("playCount"),
                "score": row.get("score"),
            }
        )
    return {
        "code": code,
        "msg": body.get("msg") or body.get("message") or body.get("error") or "",
        "records": records,
        "week_count": len(week),
        "all_count": len(all_data),
        "raw_keys": list(body.keys())[:16],
    }


def fetch_sub_artists(
    *,
    cookie: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Followed / favorite artists via weapi ``artist/sublist``."""
    body = weapi_post(
        "/weapi/artist/sublist",
        {"limit": limit, "offset": offset, "total": True},
        cookie=cookie,
    )
    code = int(body.get("code") or 0)
    raw = body.get("data") if isinstance(body.get("data"), list) else body.get("artists")
    if not isinstance(raw, list):
        raw = []
    records: list[dict[str, Any]] = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "")
        if not aid:
            continue
        records.append(
            {
                "provider_artist_id": aid,
                "artist_id": aid,
                "name": str(a.get("name") or ""),
                "album_size": a.get("albumSize"),
                "mv_size": a.get("mvSize"),
                "pic_url": str(a.get("picUrl") or a.get("img1v1Url") or ""),
            }
        )
    return {
        "code": code,
        "msg": body.get("msg") or body.get("message") or body.get("error") or "",
        "records": records,
        "has_more": bool(body.get("hasMore")),
        "count": int(body.get("count") or len(records)),
        "raw_keys": list(body.keys())[:16],
    }
