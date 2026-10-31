# -*- coding: utf-8 -*-
"""CLI: doctor | demo-sync | sync-netease | migrate-legacy | serve | register-domain | submit-graph."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mnemosyne-music")
    parser.add_argument("--data-dir", default=os.environ.get("MNEMOSYNE_MUSIC_DATA", ""))
    parser.add_argument("--mnemosyne-url", default=os.environ.get("MNEMOSYNE_BASE_URL", ""))
    parser.add_argument("--mnemosyne-token", default=os.environ.get("DSH_MEMORY_TOKEN", ""))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("demo-sync")
    p.add_argument("--user", default="owner")
    p.set_defaults(func=_cmd_demo)

    p = sub.add_parser("sync-netease", help="Real NetEase web sync (config-bind playlist id)")
    p.add_argument("--user", default="owner")
    p.add_argument("--playlist-id", required=True, help="我喜欢的音乐 歌单数字 ID")
    p.add_argument("--subject-id", default="netease_local")
    p.set_defaults(func=_cmd_netease)

    p = sub.add_parser("migrate-legacy", help="Read legacy music_memory profile → outbox evidence")
    p.add_argument("--profile", required=True, help="path to profile.json")
    p.set_defaults(func=_cmd_legacy)

    p = sub.add_parser("serve", help="HTTP /api/music/v1 (requires [http] extra)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8770)
    p.add_argument("--playlist-id", default="")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("register-domain", help="Register Music Domain Pack into Mnemosyne (ontology only)")
    p.add_argument("--pack", default="", help="path to music_domain_pack.v1.json")
    p.set_defaults(func=_cmd_register_domain)

    p = sub.add_parser(
        "submit-graph",
        help="Submit outbox + optional graph promote / preference signals (never auto-likes)",
    )
    p.add_argument("--promote-graph", action="store_true")
    p.add_argument("--preference-signals", action="store_true")
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--commit", action="store_true", help="actually write to Mnemosyne (disables dry-run)")
    p.add_argument("--user-anchor", default="", help="memory id for User endpoint of saves/listened_to")
    p.set_defaults(func=_cmd_submit_graph)

    p = sub.add_parser(
        "bootstrap",
        help="One-shot: domain → user_anchor → auth → sync → promote",
    )
    p.add_argument("--user", default="owner")
    p.add_argument("--mode", choices=("config_bind", "qr"), default="config_bind")
    p.add_argument("--playlist-id", default="", help="红心歌单 ID（config_bind）")
    p.add_argument("--subject-id", default="")
    p.add_argument("--wait-qr-sec", type=float, default=120.0)
    p.add_argument("--commit", action="store_true")
    p.add_argument("--no-promote", action="store_true")
    p.add_argument("--bank", default="mnemosyne", help="memory bank kind: mnemosyne|local_file")
    p.set_defaults(func=_cmd_bootstrap)

    p = sub.add_parser("confirm-like", help="Explicit user confirm → likes (never silent)")
    p.add_argument("--target-id", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--target-type", default="track")
    p.add_argument("--note", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--bank", default="mnemosyne")
    p.set_defaults(func=_cmd_confirm_like)

    p = sub.add_parser("reject-preference", help="Reject inferred preference signal")
    p.add_argument("--memory-id", default="")
    p.add_argument("--target-id", default="")
    p.add_argument("--note", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--bank", default="mnemosyne")
    p.set_defaults(func=_cmd_reject_pref)

    p = sub.add_parser("unconfirm-like", help="Explicit revoke of confirmed likes (not NetEase unsaved)")
    p.add_argument("--target-id", default="")
    p.add_argument("--memory-id", default="")
    p.add_argument("--title", default="")
    p.add_argument("--note", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--bank", default="mnemosyne")
    p.set_defaults(func=_cmd_unconfirm_like)

    p = sub.add_parser("monitor", help="Print Monitor snapshot JSON (or --serve with music API)")
    p.add_argument("--serve", action="store_true", help="start HTTP and open /monitor/")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8770)
    p.set_defaults(func=_cmd_monitor)

    p = sub.add_parser("probe-artists", help="Probe weapi artist/sublist with saved cookie")
    p.set_defaults(func=_cmd_probe_artists)

    args = parser.parse_args(argv)
    return int(args.func(args))


def _host(args: argparse.Namespace):
    """默认挂到 Mnemosyne Memory Bank；``--data-dir`` / ``MNEMOSYNE_MUSIC_DATA`` 可覆盖。"""
    from music_acquisition.hosts.mnemosyne_node import open_mnemosyne_host

    root = (args.data_dir or "").strip() or None
    return open_mnemosyne_host(data_dir=root)


def _print_json(obj: object) -> None:
    """Windows console may be GBK — avoid UnicodeEncodeError on JP titles."""
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))


def _cmd_doctor(args: argparse.Namespace) -> int:
    return asyncio.run(_adoctor(args))


async def _adoctor(args: argparse.Namespace) -> int:
    from music_acquisition.bank_paths import bank_layout_status
    from music_acquisition.engine import MusicAcquisitionEngine
    from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient
    from music_acquisition.playlist_scope_store import get_store

    host = _host(args)
    bank = bank_layout_status()
    if hasattr(host, "bank_status"):
        bank = host.bank_status()
    engine = MusicAcquisitionEngine(
        host,
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
    )
    status = await engine.initialize()
    scopes = get_store(Path(host.local_storage.root()) / "playlist_scopes").snapshot()
    contract = None
    if args.mnemosyne_url:
        contract = MnemosyneHttpClient(
            args.mnemosyne_url, args.mnemosyne_token
        ).contract_probe()
    print(
        json.dumps(
            {
                "engine": status.model_dump(mode="json"),
                "memory_bank": bank,
                "playlist_scopes": scopes,
                "mnemosyne_url": args.mnemosyne_url or None,
                "mnemosyne_contract": contract,
                "maturity": {
                    "netease_acquisition": "beta",
                    "mnemosyne_http": "beta",
                    "dsh_plugin": "stub",
                    "ga": False,
                    "note": "Observation/Memory/Relation usable; fix search path + domain register required for GA claim",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    await engine.shutdown()
    contract_ok = True if contract is None else bool(contract.get("ok") and contract.get("search", {}).get("ok"))
    return 0 if status.ok and contract_ok else 1


def _cmd_demo(args: argparse.Namespace) -> int:
    return asyncio.run(_ademo(args))


async def _ademo(args: argparse.Namespace) -> int:
    from music_acquisition.domain.models import (
        AuthorizationRequest,
        MusicScopeConfig,
        SyncRequest,
    )
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(_host(args))
    await engine.initialize()
    ch = await engine.authorize(AuthorizationRequest(owner_user_id=args.user, node_id="cli-demo"))
    st = await engine.get_authorization_status(ch.session_id)
    await engine.configure_scopes(
        MusicScopeConfig(
            owner_user_id=args.user,
            scopes=[
                "music.library.liked_songs",
                "music.library.playlists",
                "music.recommend.daily_songs",
                "music.history.playback",
            ],
        )
    )
    run = await engine.sync_now(
        SyncRequest(owner_user_id=args.user, collectors=["liked_songs", "daily_songs"])
    )
    recall = engine.recall(subject_id=st.subject_id or "mock_uid_1", query="喜欢")
    print(
        json.dumps(
            {
                "auth": st.model_dump(mode="json"),
                "sync": run.model_dump(mode="json"),
                "recall": recall,
                "data_root": engine._host.local_storage.root(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    await engine.shutdown()
    return 0 if run.status in ("success", "partial") else 1


def _cmd_netease(args: argparse.Namespace) -> int:
    return asyncio.run(_anetease(args))


async def _anetease(args: argparse.Namespace) -> int:
    from music_acquisition.domain.models import (
        AuthorizationRequest,
        MusicScopeConfig,
        SyncRequest,
    )
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        use_real_netease=True,
        favorite_playlist_id="",
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
    )
    await engine.initialize()
    ch = await engine.authorize(AuthorizationRequest(owner_user_id=args.user))
    bind = await engine.complete_config_bind(
        ch.session_id,
        favorite_playlist_id=args.playlist_id,
        subject_id=args.subject_id,
    )
    await engine.configure_scopes(
        MusicScopeConfig(
            owner_user_id=args.user,
            scopes=[
                "music.library.liked_songs",
                "music.library.playlists",
                "music.library.playlist_tracks",
                "music.recommend.daily_songs",
            ],
        )
    )
    run = await engine.sync_now(
        SyncRequest(
            owner_user_id=args.user,
            collectors=["liked_songs", "playlists", "daily_songs"],
        )
    )
    submit = engine.submit_evidence_outbox() if args.mnemosyne_url else {"skipped": True}
    print(
        json.dumps(
            {
                "bind": bind.model_dump(mode="json"),
                "sync": run.model_dump(mode="json"),
                "submit": submit,
                "data_root": engine._host.local_storage.root(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    await engine.shutdown()
    return 0 if run.status in ("success", "partial") else 1


def _cmd_legacy(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine
    from music_acquisition.legacy import load_legacy_profile, profile_to_evidence_candidates

    profile = load_legacy_profile(args.profile)
    if not profile:
        print(json.dumps({"ok": False, "error": "profile_missing"}, ensure_ascii=False))
        return 1
    items = profile_to_evidence_candidates(profile)
    engine = MusicAcquisitionEngine(_host(args))
    asyncio.run(engine.initialize())
    oid = engine.enqueue_evidence(items)
    print(json.dumps({"ok": True, "outbox_id": oid, "count": len(items)}, ensure_ascii=False, indent=2))
    asyncio.run(engine.shutdown())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("pip install 'mnemosyne-music-acquisition[http]'", file=sys.stderr)
        return 2
    from music_acquisition.api import create_app
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        use_real_netease=bool(args.playlist_id),
        favorite_playlist_id=args.playlist_id,
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
    )
    asyncio.run(engine.initialize())
    app = create_app(engine)
    uvicorn.run(app, host=args.host, port=int(args.port))
    return 0


def _cmd_register_domain(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
    )
    asyncio.run(engine.initialize())
    result = engine.register_music_domain(pack_path=str(args.pack or ""))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    asyncio.run(engine.shutdown())
    return 0 if result.get("ok") else 1


def _cmd_submit_graph(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine

    dry = not bool(getattr(args, "commit", False))
    engine = MusicAcquisitionEngine(
        _host(args),
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
    )
    asyncio.run(engine.initialize())
    if not args.user_anchor:
        engine.ensure_user_anchor()
    result = engine.submit_evidence_outbox(
        promote_graph=bool(args.promote_graph),
        publish_preference_signals=bool(args.preference_signals),
        dry_run_graph=dry,
        user_anchor_memory_id=str(args.user_anchor or engine._user_anchor_id or ""),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    asyncio.run(engine.shutdown())
    return 0 if result.get("ok") else 1


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    return asyncio.run(_abootstrap(args))


async def _abootstrap(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        use_real_netease=True,
        favorite_playlist_id=str(args.playlist_id or ""),
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
        memory_bank_kind=str(args.bank or "mnemosyne"),
        owner_user_id=str(args.user),
    )
    await engine.initialize()
    result = await engine.bootstrap_pipeline(
        owner_user_id=str(args.user),
        mode=str(args.mode),
        favorite_playlist_id=str(args.playlist_id or ""),
        subject_id=str(args.subject_id or ""),
        promote_graph=not bool(args.no_promote),
        preference_signals=not bool(args.no_promote),
        commit=bool(args.commit),
        wait_qr_sec=float(args.wait_qr_sec),
    )
    _print_json(result)
    await engine.shutdown()
    return 0 if result.get("ok") else 1


def _cmd_confirm_like(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
        memory_bank_kind=str(args.bank or "mnemosyne"),
    )
    asyncio.run(engine.initialize())
    result = engine.confirm_like(
        target_id=str(args.target_id),
        title=str(args.title or ""),
        target_type=str(args.target_type),
        note=str(args.note or ""),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    asyncio.run(engine.shutdown())
    return 0 if result.get("ok") else 1


def _cmd_reject_pref(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
        memory_bank_kind=str(args.bank or "mnemosyne"),
    )
    asyncio.run(engine.initialize())
    result = engine.reject_preference_signal(
        memory_id=str(args.memory_id or ""),
        target_id=str(args.target_id or ""),
        note=str(args.note or ""),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    asyncio.run(engine.shutdown())
    return 0 if result.get("ok") else 1


def _cmd_unconfirm_like(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
        memory_bank_kind=str(args.bank or "mnemosyne"),
    )
    asyncio.run(engine.initialize())
    result = engine.unconfirm_like(
        target_id=str(args.target_id or ""),
        memory_id=str(args.memory_id or ""),
        title=str(args.title or ""),
        note=str(args.note or ""),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    asyncio.run(engine.shutdown())
    return 0 if result.get("ok") else 1


def _cmd_monitor(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
        memory_bank_kind="local_file",
    )
    asyncio.run(engine.initialize())
    snap = engine.monitor_snapshot()
    if not args.serve:
        print(json.dumps(snap, ensure_ascii=False, indent=2, default=str))
        asyncio.run(engine.shutdown())
        return 0
    try:
        import uvicorn
    except ImportError:
        print("pip install 'mnemosyne-music-acquisition[http]'", file=sys.stderr)
        return 2
    from music_acquisition.api import create_app

    app = create_app(engine)
    print(f"Monitor UI → http://{args.host}:{args.port}/monitor/", flush=True)
    uvicorn.run(app, host=args.host, port=int(args.port))
    return 0


def _cmd_probe_artists(args: argparse.Namespace) -> int:
    return asyncio.run(_aprobe_artists(args))


async def _aprobe_artists(args: argparse.Namespace) -> int:
    from music_acquisition.engine import MusicAcquisitionEngine

    engine = MusicAcquisitionEngine(
        _host(args),
        use_real_netease=True,
        mnemosyne_base_url=args.mnemosyne_url,
        mnemosyne_token=args.mnemosyne_token,
        owner_user_id="owner",
    )
    await engine.initialize()
    await engine._auth.restore_credentials("owner")
    src = engine.source
    getter = getattr(src, "get_favorite_artists", None)
    if not callable(getter):
        print(json.dumps({"ok": False, "error": "no_get_favorite_artists"}, ensure_ascii=False))
        await engine.shutdown()
        return 1
    result = await getter()
    out = {
        "ok": result.status in ("success", "partial"),
        "status": result.status,
        "count": result.actual_count,
        "warnings": result.warnings,
        "sample": (result.records or [])[:5],
        "source_metadata": result.source_metadata,
    }
    _print_json(out)
    await engine.shutdown()
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
