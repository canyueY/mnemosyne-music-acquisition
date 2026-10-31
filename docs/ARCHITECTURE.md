# Mnemosyne Music Acquisition — Architecture

**文档版本：** 1.0  
**设计状态：** Architecture Proposal + Phase 1 Skeleton Implemented  
**包路径：** mnemosyne-music-acquisition/（独立组件模块）  
**适用：** Mnemosyne / DSH / 未来 dsh-mnemosyne-music  
**部署：** Dual-Mode（DSH Host + Standalone Mnemosyne Node）  
**定位：** Independent Music Acquisition Infrastructure  

**原则：** Zero Extra User Tools · Host Agnostic · Authorization First · Local First · Evidence Driven

> 「零工具依赖」= 零**额外用户工具**；不等于零运行时 / 零网络 / 零宿主。

---

## 1. 目标与非目标

### 目标

- 用户喜欢歌曲、歌单与曲目、每日推荐、播放历史、收藏歌手、元数据、明确偏好与可获取反馈。
- 双模式共用同一 Engine；扫码授权（不收集密码）；自动同步与完整性校验；采集缓存与 Mnemosyne LT 分离；权限可撤销；接口可替换。

### 非目标（v1）

1. 绕过登录 / 未授权私人数据  
2. 破解风控或未允许的客户端模拟  
3. 保证网易云接口永久可用 / 全账号全数据  
4. 无事件源时的「真实时」播放监控  
5. 独立于 Mnemosyne 的长期音乐画像库  
6. DSH 作为 Node 必要条件  
7. 社区 API 作为永久基础设施  

---

## 2. 五层结构

1. Host Integration Layer  
2. **Music Acquisition Engine**（核心）  
3. Source Adapter Layer（Netease）  
4. Acquisition Data Layer（Cache / Snapshot / Sync State）  
5. Mnemosyne Cognitive Integration Layer（Observation / Evidence / Memory via Service API）

Engine **不属于** DSH，也 **不属于** Cognitive Core。

`	ext
User → Auth UI → DSH Host | Mnemosyne Node
         → HostRuntime → Music Acquisition Engine
         → Auth | Planner | Sync Coordinator
         → Netease Source Adapter → Authorized Network API
         → Credential / Snapshot / Sync stores
         → Music Memory Adapter → Mnemosyne Service API
`

### 原则

1. Engine 独立于宿主（只经 HostRuntime）  
2. 数据源适配与采集流程分离  
3. 采集与记忆分离  
4. 授权与能力分离  
5. 完整性优先（partial 不覆盖 complete）  
6. 故障隔离  

---

## 3. Dual-Mode

### Mode A — DSH 内置

DSH Plugin dsh-mnemosyne-music 加载 Engine；Mnemosyne Node 管 LT。禁止插件直连 Node DB。

### Mode B — Mnemosyne 独立

Node Component Runtime 加载 mnemosyne.music-acquisition；不依赖 DSH 配置/调度/插件 API/身份/数据库。

### 统一 Engine API

initialize · uthorize · getAuthorizationStatus · 
evokeAuthorization · configureScopes · syncNow · getSyncStatus · getSnapshots · getDiff · shutdown

宿主只实现 HostRuntime：credentialStore · 	askScheduler · eventPublisher · localStorage · httpTransport · userInteraction · logger · clock

Python 映射见 src/music_acquisition/contracts/。

---

## 4. 模块树（本仓已落地骨架）

`	ext
music_acquisition/
├── engine/          # MusicAcquisitionEngine
├── auth/            # AuthManager
├── source/          # Mock + netease/(Phase2)
├── sync/            # SyncCoordinator（唯一入口）
├── snapshot/        # hash / diff / SnapshotManager
├── governance/      # scopes 默认全关
├── memory/          # Evidence 映射 + outbox（不写 LT DB）
├── hosts/           # in_memory / mnemosyne_node stub / dsh stub
├── contracts/       # HostRuntime · MusicSourceAdapter
└── cli.py
`

Playback / 完整 collectors / observability 按 Phase 推进；边界先稳定。

---

## 5. 授权

- 首选二维码；禁止 Agent 收密码；禁止 Cookie 进 Prompt/日志/快照。  
- Session：created → waiting_scan → waiting_confirm → authorized | expired | failed | cancelled  
- CredentialStore 与快照库分离；建议 OS 密钥环。  
- 绑定区分：Node Owner · Netease Subject · Host Agent · Component  

---

## 6. Scope / Consent

采集 Scope（默认全关）示例：

music.profile.basic · music.library.liked_songs · music.library.playlists · music.library.playlist_tracks · music.library.favorite_artists · music.recommend.daily_songs · music.recommend.daily_playlists · music.history.playback · music.playback.events · music.playback.control

使用权限另计：
ead · cache · submit_evidence · create_memory · 
ecall · export · delete

检查顺序：Caller → Node Ownership → Component Capability → Scope → Consent → Credential → Retention → Execute  

撤销：停采集、删凭证、记审计；**不**自动删 Mnemosyne LT（删记忆走独立流程）。

---

## 7–9. 采集 / 快照 / Diff

- 每类数据独立 Collector；分页、游标、页重试、去重、总数校验。  
- Canonical Model：MusicTrack · MusicArtist · MusicPlaylist（保留 provider id + schema_version）。  
- 三类存储：Credential · Acquisition Cache · Mnemosyne Memory（禁止混用）。  
- 每日推荐键：{subjectId}:{localDate}；用户时区；失败保留旧完整快照。  
- snapshot_hash = SHA256(canonical_json(records))  
- Diff：dded|removed|metadata_changed|membership_*|playlist_* + Tombstone  

---

## 10. 同步

触发：scheduled|manual|startup_recovery|auth_recovered|scope_changed|retry — **同一 Coordinator**。  

状态机：idle→queued→checking_auth→fetching→validating→computing_diff→committing→submitting_evidence→success|partial|failed  

锁：
ode+owner+provider+data_type+subject  

重试：指数退避；登录失效/权限不足/unsupported **不**盲重试。

---

## 11. Mnemosyne 接入

流：API → Raw → Normalized → Observation → Evidence → Evaluation → Memory → Recall  

- 推荐曝光 ≠ 喜欢  
- 单次播放 ≠ 长期偏好  
- 显式表达优先；冲突保留证据链并可选确认  
- Memory Adapter **禁止**直连 sqlite  

---

## 12–13. 播放与实时事件

播放为可选子模块；执行后必须校验实际播放目标。  
实时事件须有真实事件源；轮询状态变化不得伪装成精确客户端操作。

---

## 14. 旧模块迁移

| 现有 | 归属 |
| --- | --- |
| Murasame/netease_music.py | Netease Source Adapter |
| Murasame/music_library.py | Snapshot / Library Cache |
| Murasame/music_memory.py | Legacy Migration Adapter |

步骤：审计 → 归属 → Canonical → 适配器 → 快照/记忆迁移 → 校验 → 切写 Mnemosyne → 兼容期 → 再考虑移除。

---

## 15. API / Tools（规划）

前缀：/api/music/v1/{auth,scopes,acquisition,sync,snapshots,playback,recall,admin}  

Agent Tools：music_auth_* · music_scopes_* · music_sync_* · music_daily_list · music_play_* · music_recall …  
禁止把 Cookie / 任意原始登录 API 暴露给 Agent。

---

## 16. Source Adapter

Capability Discovery 强制；社区 NeteaseCloudMusicApi 仅可作研究/可选本地依赖，**不是**官方 API，不可默认公共实例存凭证。不合规则标 unsupported。

---

## 17–19. 降级 / 安全 / 观测

见方案原文矩阵。日志禁 Cookie/Token/密码/完整私人列表。删除动作必须拆分（缓存 / 快照 / outbox / evidence / memory / 授权）。

---

## 20. 验收（Phase 1 已覆盖部分）

- [x] 无 DSH / 无网易云可跑 Engine + Mock  
- [x] Scope 默认拒绝采集  
- [x] partial 不覆盖 complete  
- [x] 推荐证据 llow_create_memory=false  
- [ ] 真实扫码授权（Phase 2）  
- [ ] 双模式宿主集成（Phase 5）  

---

## 21. 路线图

| Phase | 交付 | 状态 |
| --- | --- | --- |
| 0 | Audit / Capability Matrix / Migration Map | **完成**（docs/MUSIC_*） |
| 1 | Engine + Contracts + Mock + Tests | **完成** |
| 2 | Netease HTTP Adapter + config-bind + Collectors | **完成**（QR=unsupported 诚实裁切） |
| 3 | Snapshot / Diff / TTL / Outbox / Recovery | **完成** |
| 4 | Evidence Submit + Recall | **完成** |
| 5 | Filesystem Host + REST `/api/music/v1` + DSH stub | **完成**（正式 npm 插件另发） |
| 6 | Playback optional bridge | **完成**（无宿主则 unsupported） |
| 7 | GA checklist | **完成**（见 docs/GA_CHECKLIST.md） |

### Phase 5 完成定义（补全）

1. DSH Host Adapter 实现完整 HostRuntime  
2. Mnemosyne Node Adapter 使用独立 data_dir/music-acquisition/  
3. 插件与 Component 清单可安装/卸载且不删用户快照（除非显式）  
4. 双模式契约测试结果一致  
5. DSH 关闭时 Mode B 仍可授权与同步  

---

## 实现入口

`at
py -3 -m music_acquisition.cli doctor
py -3 -m music_acquisition.cli demo-sync
py -3 -m pytest -q
`

组件清单：mnemosyne.component.json → mnemosyne.music-acquisition
