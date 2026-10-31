# MUSIC_CURRENT_STATE_AUDIT

**日期：** 2026-10-31  
**范围：** 桌宠 `Murasame/netease_music.py` · `music_library.py` · `music_memory.py` · `netease_cdp.py`

## 1. 现有资产

| 模块 | 规模 | 角色 |
| --- | --- | --- |
| `netease_music.py` | ~3889 行 | 播放意图、桌面/CDP/ncm-cli、网页歌单 API、日推拉取、红心歌单（需配置 ID） |
| `music_library.py` | ~917 行 | 本地收藏/日推/雷达快照缓存、分类、日调度 |
| `music_memory.py` | ~887 行 | **本地**听歌画像/事件/口味注入（非 Mnemosyne LT） |
| `netease_cdp.py` | — | 桌面客户端 CDP 播放/当前曲探测 |
| `music_recognition.py` | ~289 行 | 听歌识曲（旁路） |

## 2. 数据路径（旧）

| 数据 | 典型路径 |
| --- | --- |
| 音乐库缓存 | 桌宠 `data/music_library/`（favorites / daily / radar） |
| 口味画像 | `data/music_memory/profile.json` + 事件 jsonl |
| 网易云配置 | `config.json` → `netease_music.*`（含 `favorite_playlist_id`） |

## 3. 依赖

| 依赖 | 用途 | 用户是否需额外包 |
| --- | --- | --- |
| 桌面网易云 + CDP:9222 | 播放控制 / 部分状态 | 已有客户端即可；非额外「脚本包」 |
| ncm-cli（可选） | 登录态 / 搜索 encrypted-id | 可选工具；**违反「零额外工具」时不可作为硬依赖** |
| 公开网页 API | 歌单详情、曲目 detail、日推池 | 仅 HTTP；日推文档写明**无需登录、为通用池** |
| `favorite_playlist_id` | 红心列表 | 需用户配置歌单数字 ID |

## 4. 关键缺口（相对 Music Acquisition 方案）

1. **无内建二维码授权会话**（无 unikey/QR 轮询实现）。  
2. **无完整私人播放历史同步**。  
3. **无**与 Mnemosyne Evidence/Memory 的正式 Service 提交。  
4. `music_memory` 与 Mnemosyne **双权威风险**（必须降级为 Legacy Adapter）。  
5. 采集逻辑散落在宿主模块，未抽成 Host-agnostic Engine。
