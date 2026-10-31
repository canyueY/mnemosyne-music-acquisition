# MUSIC_SOURCE_CAPABILITY_MATRIX

**更新：** 2026-10-31 实机扫码探测（见 `LIVE_PROBE_QR_DAILY_HISTORY.md`）

| Feature | 现状 | 证据 | Acquisition Adapter 策略 |
| --- | --- | --- | --- |
| qrLogin | **Implemented** | `api/login/qrcode/unikey` + `client/login` 801/802/803 | `qr_auth.py` + Adapter poll |
| credentialRefresh | Unsupported | — | false |
| likedSongs | **Partial→增强** | 登录后可猜「我喜欢的音乐」playlist；或 config id | cookie 或 `favorite_playlist_id` |
| playlists | **Implemented**（需登录） | `api/user/playlist` code=200 | cookie 采集 |
| playlistTracks | **Implemented** | `api/v6/playlist/detail` + trackIds 补取 | Collector |
| dailySongs | **Implemented** | 无 cookie=公开池；有 cookie=个性化（overlap 0） | `get_daily_songs` 分支 |
| dailyPlaylists | Unsupported | — | false |
| playbackHistory | **Supported (weapi)** | `/weapi/v1/play/record` + Cookie；plain `/api` 仍 404 | 需 QR/cookie |
| favoriteArtists | **Supported (weapi)** | `/weapi/artist/sublist` + Cookie | `probe-artists` / collector `favorite_artists` |
| playbackControl | Partial | 桌宠 CDP 桥接 | Playback 可选 |
| realtimeEvents | Partial | CDP 轮询 | 不得伪装精确事件 |

`verified_at` 由 Adapter `get_capabilities()` 写入。
