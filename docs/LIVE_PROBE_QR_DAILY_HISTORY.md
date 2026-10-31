# 实测报告：QR / 个性化日推 / 播放历史

**时间：** 2026-10-31  
**脚本：** `scripts/probe_qr_personal_history.py`  
**原始报告：** `tmp/qr_probe/report.json`（本地，含脱敏结论）

## 结果摘要

| 能力 | 结果 | 证据 |
| --- | --- | --- |
| 真实二维码登录 | **成功** | unikey → poll `801` 等待 → `802` 确认 → `803` 授权；账号 `MC_WL` / uid `1502617411` |
| 个性化每日推荐 | **成功** | 登录后日推 34 首；与未登录公开池 **overlap=0**，`likely_personalized=true` |
| 播放历史 | **plain 失败；weapi 已接入** | `/api/v1/user/record` → 404；采集改走 `/weapi/v1/play/record`（0.4.0） |

## 未登录对照

- 公开日推：`/api/v3/discovery/recommend/songs` code=200，30 首（如「爱」「一个人想着一个人」…）
- 登录后样本：死ぬな！ / i-mage / Pebble / Wings of Piano / Runaway Baby …

## 安全

- 探测脚本**不打印**完整 `MUSIC_U`
- Cookie 仅写本地 `tmp/qr_probe/session.cookie`（已 gitignore `tmp/`）

## 复现

```bat
cd mnemosyne-music-acquisition
set PYTHONPATH=src
py -3 scripts\probe_qr_personal_history.py --wait-sec 120
```

用网易云 App 扫弹出的 `tmp/qr_probe/netease_login_qr.png` 并确认。
