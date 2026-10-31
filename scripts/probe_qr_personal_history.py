# -*- coding: utf-8 -*-
"""Live probe: QR login → personalized daily → playback history.

Usage:
  set PYTHONPATH=src
  py -3 scripts/probe_qr_personal_history.py --wait-sec 120

Does NOT print full MUSIC_U cookie.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_acquisition.source.netease import http_client as anon
from music_acquisition.source.netease import qr_auth


def _mask_cookie(c: str) -> str:
    if not c:
        return ""
    if "MUSIC_U=" in c:
        return "MUSIC_U=<redacted>; (+other cookies)"
    return f"<cookie_len={len(c)}>"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-sec", type=int, default=120)
    ap.add_argument("--poll-sec", type=float, default=2.0)
    ap.add_argument(
        "--out-dir",
        default=str(ROOT / "tmp" / "qr_probe"),
    )
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    report: dict = {"steps": []}

    # A) anonymous daily baseline
    anon_daily = anon.fetch_daily_songs(limit=30)
    anon_ids = [t["provider_track_id"] for t in anon_daily]
    report["steps"].append(
        {
            "id": "anon_daily",
            "ok": True,
            "count": len(anon_ids),
            "sample_ids": anon_ids[:5],
        }
    )
    print(json.dumps(report["steps"][-1], ensure_ascii=False, indent=2))

    # B) QR session
    sess = qr_auth.create_qr_session()
    png = out / "netease_login_qr.png"
    qr_auth.save_qr_png(sess.scan_url, str(png))
    report["steps"].append(
        {
            "id": "qr_created",
            "ok": True,
            "unikey_prefix": sess.unikey[:8],
            "scan_url": sess.scan_url,
            "qr_png": str(png),
        }
    )
    print(json.dumps(report["steps"][-1], ensure_ascii=False, indent=2))
    print("\n请用【网易云音乐 App】扫码并在手机上确认登录…")
    print(f"二维码文件: {png}")
    try:
        os.startfile(str(png))  # Windows
    except OSError:
        webbrowser.open(png.as_uri())

    deadline = time.time() + max(15, int(args.wait_sec))
    while time.time() < deadline:
        sess = qr_auth.poll_qr(sess)
        code = sess.raw_last.get("code")
        print(f"  poll code={code} status={sess.status}")
        if sess.status in ("authorized", "expired", "failed"):
            break
        time.sleep(max(0.5, float(args.poll_sec)))

    qr_step = {
        "id": "qr_result",
        "status": sess.status,
        "last_code": sess.raw_last.get("code"),
        "message": sess.raw_last.get("message") or sess.raw_last.get("msg"),
        "cookie": _mask_cookie(sess.cookie),
        "profile": {
            "userId": (sess.profile or {}).get("userId"),
            "nickname": (sess.profile or {}).get("nickname"),
        },
    }
    report["steps"].append(qr_step)
    print(json.dumps(qr_step, ensure_ascii=False, indent=2))

    if sess.status != "authorized" or not sess.cookie:
        report["ok"] = False
        report["conclusion"] = (
            "QR 未完成授权（超时/取消/过期）。"
            "接口侧 unikey+801 轮询可用；需用户扫码确认后才能测个性化日推与历史。"
        )
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(report["conclusion"])
        return 2

    # C) personalized daily
    daily = qr_auth.fetch_daily_with_cookie(sess.cookie, limit=30)
    auth_ids = [str(x["id"]) for x in daily.get("sample") or [] if x.get("id") is not None]
    # full id list for overlap — refetch count already in daily
    # re-get anonymously already have anon_ids; need full auth list
    body_ids = []
    try:
        # expand: call again via fetch_daily_songs path is anon-only; use raw
        raw, _ = qr_auth._request(qr_auth.DAILY, cookie=sess.cookie)
        songs = ((raw.get("data") or {}).get("dailySongs") or [])
        body_ids = [str(s.get("id")) for s in songs if isinstance(s, dict) and s.get("id")]
    except Exception as exc:  # noqa: BLE001
        body_ids = auth_ids
        daily["expand_error"] = str(exc)

    overlap = len(set(anon_ids) & set(body_ids))
    daily_step = {
        "id": "daily_authed",
        "code": daily.get("code"),
        "count": daily.get("count") or len(body_ids),
        "sample": daily.get("sample"),
        "overlap_with_anon": overlap,
        "anon_count": len(anon_ids),
        "likely_personalized": overlap < max(1, len(anon_ids) // 2),
    }
    report["steps"].append(daily_step)
    print(json.dumps(daily_step, ensure_ascii=False, indent=2))

    # D) playback history
    uid = (sess.profile or {}).get("userId")
    hist = {"code": None, "msg": "no_uid"}
    if uid:
        hist = qr_auth.fetch_user_record(sess.cookie, uid, kind=1)
    hist_step = {
        "id": "playback_history",
        "uid": uid,
        "code": hist.get("code"),
        "msg": hist.get("msg"),
        "week_count": hist.get("week_count"),
        "all_count": hist.get("all_count"),
        "sample": hist.get("sample"),
        "ok": int(hist.get("code") or 0) == 200,
    }
    report["steps"].append(hist_step)
    print(json.dumps(hist_step, ensure_ascii=False, indent=2))

    report["ok"] = True
    report["conclusion"] = {
        "qr_login": "ok",
        "personalized_daily": daily_step.get("likely_personalized"),
        "playback_history": hist_step.get("ok"),
    }
    # persist cookie securely-ish for follow-up (local tmp only)
    (out / "session.cookie").write_text(sess.cookie, encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("报告已写:", out / "report.json")
    print("结论:", json.dumps(report["conclusion"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
