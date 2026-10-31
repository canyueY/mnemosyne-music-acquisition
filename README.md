# Mnemosyne Music Acquisition

**网易云音乐**数据采集、点播控制桥与「音乐认知」接入层。

> **重要：目前只适配网易云 Music（Netease Cloud Music）。**  
> 源适配器、授权、采集、点歌 / 红心 / 下一曲等播放桥，均按网易云桌面端 / Web API / weapi 实现。  
> **不支持** QQ 音乐、Spotify、Apple Music 等其它平台（架构上可换 SourceAdapter，但仓库内尚未实现）。

把听歌行为变成可治理的 Evidence / Observation / 候选记忆，再交给记忆库做长期认知——**默认对接 Mnemosyne**，也可用同一套接口接到其它记忆库。Host 运行时抽象（Host-agnostic）；**音乐源当前仅网易云**。

| | |
| --- | --- |
| 包名 | `mnemosyne-music-acquisition`（Python：`music_acquisition`） |
| 版本 | **0.4.2** |
| 组件 ID | `mnemosyne.music-acquisition` |
| **音乐源** | **仅网易云（Netease）** |
| 成熟度 | **Beta**：网易云采集 + Mnemosyne HTTP 证据上行可用；**不是** GA 级 3.0 完整适配；DSH 仅为占位壳 |
| 许可 | [AGPL-3.0-or-later](LICENSE) |
| 原则 | 安装 ≠ 授权 ≠ 同步 · **永不静默写入 likes / confirmed** |

---

## 它做什么

| 能力 | 说明 |
| --- | --- |
| 授权（网易云） | **QR 扫码** 或 **config-bind**（红心歌单 ID）；Cookie 落 Memory Bank `credentials/`（不进日志） |
| 采集（网易云） | 红心 / 歌单 / 日推 / 播放历史 / 收藏歌手 → 本地 snapshot |
| 点播与控制（网易云，可选桥） | 点歌、红心歌单、日推、歌单内下一曲/上一曲、序号往返；见下节 |
| 对账 | 取消收藏 → diff → revoke；**不自动**撕 confirmed likes |
| 音乐认知本体 | 安装/注册 `domain/music_domain_pack.v1.json` → Mnemosyne `CognitiveSchema`（仅本体，无用户偏好） |
| 证据上行 | Outbox → Observation（`metadata.domain=music`） |
| 图谱 | 可选 promote：`saves` / `listened_to` / `contains`（**禁止**自动 `likes`） |
| 偏好 | PreferenceSignal = **inferred**；`confirm-like` / `unconfirm-like` 才动 confirmed / likes |
| 桌宠口令 | 「播放红心 / 下一首 / 确认喜欢 / 同步音乐记忆」等（网易云客户端） |
| 运维 | 最小 Monitor 页：`/monitor/` |

诚实能力表与实机报告见 [`docs/MUSIC_SOURCE_CAPABILITY_MATRIX.md`](docs/MUSIC_SOURCE_CAPABILITY_MATRIX.md)、[`docs/LIVE_PROBE_QR_DAILY_HISTORY.md`](docs/LIVE_PROBE_QR_DAILY_HISTORY.md)。

---

## 点歌 · 红心 · 收藏 · 切歌（仅网易云）

本包分两条线，**都只认网易云**：

1. **采集线（本仓库核心）**：拉红心列表 / 歌单 / 日推 / 历史 / 关注歌手 → snapshot → 记忆库。  
2. **播放控制线（可选）**：通过 `MusicPlaybackService` 桥接本机 **网易云桌面客户端**（常配合 Murasame / [netease-cdp](https://github.com/canyueY/netease-cdp)）。无桌宠 / 无客户端时返回 `PLAYBACK_UNSUPPORTED`，**采集仍可独立工作**。

| 功能 | 含义（网易云） | 采集 | 播放控制 |
| --- | --- | --- | --- |
| **点歌** | 按曲目 ID / 歌名意图打开并播放 | — | `play_song` · `POST /playback/song` |
| **红心歌单** | 「我喜欢的音乐」整单播放；config `favorite_playlist_id` | `liked_songs` 同步 | 桌宠 `play_favorites` / 桥接 playlist |
| **收藏 / 红心行为** | 用户在客户端收藏过的曲目 → `action=saved`（observed，**不是**自动 likes） | ✅ sync + outbox | 确认喜欢另走 `confirm-like` |
| **取消收藏** | 红心列表少了的歌 → revoke / supersede `saves` | ✅ diff | 不自动 `unconfirm-like` |
| **下一曲 / 上一曲** | **当前源歌单**内邻曲（日推 / 红心 / 歌单），不是乱跳全局队列 | playlist_scopes 边界 | `play_next_in_scope` · `POST /playback/next` |
| **按序号点播** | 「第 N 首」↔ track 往返查找 | scope bank | `roundtrip` / playback playlist+index |
| **每日推荐** | 登录后个性化日推；未登录为公开池 | `daily_songs` | 桌宠 / CDP 日推播放 |
| **收藏歌手** | 关注的艺人列表 | `favorite_artists`（需 Cookie） | — |
| **暂停 / 继续 / 音量** | 客户端媒体键级控制 | — | 桌宠网易云意图（非本包采集 API） |

**边界约定（避免「歌单第 N 首」点错）**

- 「下一首」优先锚定 **源歌单**（`source:playlist:…` / `source:daily_recommend`），再才是 runtime 播放队列。  
- 边界存在 Memory Bank：`music-acquisition/playlist_scopes/`。

**HTTP 示例（需本机网易云 + 播放桥可用）**

```http
POST /api/music/v1/playback/song?track_id=1470902634
POST /api/music/v1/playback/next?delta=1
POST /api/music/v1/playback/next?delta=-1
POST /api/music/v1/playlist/roundtrip
```

桌宠侧口语（同样 **只驱动网易云**）：「放红心歌单」「下一首」「播放某某歌」「确认喜欢这首歌」等。

---

## 与 Mnemosyne「两个组件」如何适配

本仓库是 **Music Acquisition 组件**；长期记忆与领域闸门在 **Mnemosyne Node（+ Memory Bank）**。

```text
┌─────────────────────────────┐     HTTP Service Contract      ┌──────────────────────────────┐
│  mnemosyne-music-acquisition │  ───────────────────────────►  │  Mnemosyne Node               │
│  · 授权 / 拉歌 / snapshot    │  observations / memories /     │  · CognitiveSchema (domain)   │
│  · outbox / graph plan       │  relations / domains/register  │  · memory.db + 检索闸门       │
│  · MemoryBankBackend 默认    │                                │  · Feedback / ChangePlan      │
└──────────────┬──────────────┘                                └──────────────▲───────────────┘
               │ 本地运行时数据                                                │
               ▼                                                              │
┌─────────────────────────────┐                                               │
│  Mnemosyne Memory Bank      │  %LOCALAPPDATA%\Mnemosyne Memory Bank\         │
│  · data/memory.db（Node）   │◄──────────────────────────────────────────────┘
│  · music-acquisition/       │  本组件缓存：snapshots / outbox / credentials /
│    （采集侧，不直连 sqlite） │  playlist_scopes / memory_bank_mirror
└─────────────────────────────┘
```

| 组件 | 仓库 / 角色 | 本包如何接 |
| --- | --- | --- |
| **Mnemosyne Node** | [`canyueY/dsh-mnemosyne`](https://github.com/canyueY/dsh-mnemosyne)（现行）；归档参考 [`canyueY/mnemosyne`](https://github.com/canyueY/mnemosyne) | `MNEMOSYNE_BASE_URL` + `DSH_MEMORY_TOKEN`；`register-domain` / `bootstrap --commit` |
| **Memory Bank** | [`canyueY/mnemosyne-memory-bank`](https://github.com/canyueY/mnemosyne-memory-bank) 概念与布局 | 默认数据根：`…/Mnemosyne Memory Bank/music-acquisition/`（可用 `MNEMOSYNE_MUSIC_DATA` 覆盖） |

**硬规则**

1. 本组件 **禁止** 直接打开 Node 的 `memory.db`。
2. **安装 Domain Pack** 只注册本体（Track / Artist / …）；**不同步**用户红心进长期 likes。
3. 检索：无关 query 排除 `metadata.domain=music`；音乐意图或 `domain=music` 才召回（Node 侧闸门）。

**与当前 Mnemosyne 3.x 的契约（0.4.2）**

| 路径 | 状态 |
| --- | --- |
| Observation / Memory / Relation / Feedback / Health | 可用 |
| 搜索 | **`POST /api/v1/memories/search`**（已修复错误的 `/api/v1/search`）+ `domain` |
| Domain Pack | `POST /api/v1/domains/register`（**inline pack**）；缺路由则诚实失败，bootstrap 不计成功 |
| DSH 插件 | **占位**（`hosts/dsh-mnemosyne-music` README + stub Host），以 Python/REST 为准 |

对外表述：支持网易云采集、快照、证据上行与可选播放桥；默认对接 Mnemosyne HTTP；Search / Domain Pack 客户端已对齐现行 Node，但 Cookie 加密、调度、Claim、DSH 发布仍未 GA。

契约全文：[`docs/MUSIC_DOMAIN_INTEGRATION_CONTRACT.md`](docs/MUSIC_DOMAIN_INTEGRATION_CONTRACT.md)。

### 最小联调（已实机验证过的路径）

```bat
REM 1) 启动 Mnemosyne Node，数据目录指向 Memory Bank
set DSH_MEMORY_DATA_DIR=%LOCALAPPDATA%\Mnemosyne Memory Bank
set DSH_MEMORY_TOKEN=<your-token>
py -3 -m cognitive_memory.main --host 127.0.0.1 --port 18765 --data-dir "%DSH_MEMORY_DATA_DIR%" --token %DSH_MEMORY_TOKEN%

REM 2) 本组件
set MNEMOSYNE_BASE_URL=http://127.0.0.1:18765
set DSH_MEMORY_TOKEN=<same-token>
set PYTHONPATH=src
py -3 -m music_acquisition.cli register-domain
py -3 -m music_acquisition.cli bootstrap --mode config_bind --playlist-id <红心歌单ID> --commit
```

成功时：Memory Bank 内出现 `domain:music` 等 CognitiveSchema；outbox 提交 Observation；可选 promote 写入候选记忆（仍非 likes）。

---

## 与其它记忆库的适配

长期写入走抽象接口 **`MemoryBankBackend`**（[`src/music_acquisition/memory/bank_backend.py`](src/music_acquisition/memory/bank_backend.py)）：

| Backend | `kind` | 用途 |
| --- | --- | --- |
| `MnemosyneBankBackend` | `mnemosyne` | 默认：HTTP → Node Service Contract |
| `LocalFileBankBackend` | `local_file` | 离线 / 无 Node：JSON 落在 Bank 的 `memory_bank_mirror/` |
| 自定义 | 实现同一 ABC | 其它厂商记忆库：实现 `propose_memory` / `link_relation` / `feedback` / `observe` / `search` 等即可 |

工厂：

```python
from music_acquisition.memory.bank_backend import open_memory_bank

bank = open_memory_bank(
    "mnemosyne",  # 或 "local_file"
    mnemosyne_base_url="http://127.0.0.1:18765",
    mnemosyne_token="...",
    local_root=".../music-acquisition/memory_bank_mirror",
)
```

CLI：`--bank mnemosyne|local_file`。无 `MNEMOSYNE_BASE_URL` 时引擎会降级到 `local_file`，避免误以为已写入 Node。

---

## 安装

### 要求

- Python **≥ 3.10**
- 可选：本机已部署的 Mnemosyne Node + Memory Bank
- 可选：`cryptography`（播放历史 / 收藏歌手 weapi，已写入主依赖）
- 可选：`qrcode[pil]`（扫码 PNG）、`fastapi`/`uvicorn`（HTTP / Monitor）

### 从源码

```bat
git clone https://github.com/canyueY/mnemosyne-music-acquisition.git
cd mnemosyne-music-acquisition
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,qr,http]"
set PYTHONPATH=src
py -3 -m unittest discover -s tests -q
py -3 -m music_acquisition.cli doctor
```

### 作为 Mnemosyne 组件

清单：[`mnemosyne.component.json`](mnemosyne.component.json)  
`domain_packs` → `domain/music_domain_pack.v1.json`  
安装时仅注册本体；用户数据仍须单独 authorize + sync。

### 桌宠（MurasamePet）

同级目录检出本仓库后，桌宠通过 `Murasame.music_acquisition_bridge` 调用。`config.json` 示例：

```json
{
  "music_acquisition": {
    "mnemosyne_url": "http://127.0.0.1:18765",
    "mnemosyne_token": "<token>",
    "favorite_playlist_id": "<红心歌单数字ID>",
    "memory_bank_kind": "mnemosyne",
    "commit": false
  },
  "netease_music": {
    "favorite_playlist_id": "<同上>"
  }
}
```

口令示例：「同步音乐记忆」「确认喜欢这首歌」「取消确认喜欢」。

---

## CLI 速查

| 命令 | 作用 |
| --- | --- |
| `doctor` | 引擎 / Bank 布局 / 可选 Mnemosyne URL |
| `register-domain` | 注册 Music Domain Pack（幂等） |
| `bootstrap` | Domain → user_anchor → 授权 → sync → promote |
| `submit-graph` | 提交 outbox；可选 `--promote-graph` / `--preference-signals` / `--commit` |
| `confirm-like` / `unconfirm-like` | 显式确认 / 撤销 likes |
| `reject-preference` | 拒绝 inferred 信号 |
| `probe-artists` | 探测收藏歌手（需 Cookie） |
| `monitor` / `monitor --serve` | 快照 JSON 或打开 Monitor UI |
| `serve` | REST `/api/music/v1` |

全局参数：`--data-dir`、`--mnemosyne-url`、`--mnemosyne-token`。

---

## HTTP（可选）

```bat
pip install -e ".[http]"
py -3 -m music_acquisition.cli serve --port 8770
```

常用路径：`/api/music/v1/bootstrap`、`/preference/confirm`、`/preference/unconfirm`、`/monitor/snapshot`，浏览器打开 `/monitor/`。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 总架构 |
| [MUSIC_DOMAIN_INTEGRATION_CONTRACT.md](docs/MUSIC_DOMAIN_INTEGRATION_CONTRACT.md) | 与 Node / Domain 契约 |
| [MUSIC_SOURCE_CAPABILITY_MATRIX.md](docs/MUSIC_SOURCE_CAPABILITY_MATRIX.md) | 源能力矩阵 |
| [GA_CHECKLIST.md](docs/GA_CHECKLIST.md) | 验收清单 |
| [CHANGELOG.md](CHANGELOG.md) | 版本记录 |

---

## 安全与隐私

- 不向 Agent / 日志打印完整 `MUSIC_U` / Cookie / Token。
- `credentials/`、`.env`、本地 Bank 路径勿提交（见 `.gitignore`）。
- `likes` / `confirmed` **只允许**用户显式确认路径写入。

---

## 相关项目

- [dsh-mnemosyne](https://github.com/canyueY/dsh-mnemosyne) — Mnemosyne Node + DSH 适配  
- [mnemosyne-memory-bank](https://github.com/canyueY/mnemosyne-memory-bank) — Memory Bank 说明  
- [netease-cdp](https://github.com/canyueY/netease-cdp) — 网易云桌面 CDP 播放控制（与本包播放桥互补；**同样仅网易云**）

---

## License

[AGPL-3.0-or-later](LICENSE) © canyueY
