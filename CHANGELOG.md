# Changelog

## 0.4.2

- **P0 契约修复**：搜索改为 `POST /api/v1/memories/search`（不再打错误的 `/api/v1/search`）；客户端仍传 `domain` 并按 `metadata.domain` 二次过滤
- Domain Pack：本地加载后 **inline** 提交；Node 缺路由时返回诚实错误 `domains_register_unsupported`；bootstrap 在 Mnemosyne 配置下要求 `domain_ok`
- revoke / recall / reject / unconfirm 共用修复后的 search；revoke 按 `(track_id)` 匹配 supersede
- `doctor` 增加 `mnemosyne_contract` 探针；契约单测 `test_mnemosyne_contract_paths`
- 文档：ARCHITECTURE / GA / README 成熟度表述对齐（Beta，非 GA；DSH 为占位）

## 0.4.1


- 显式 `unconfirm-like`（撕 likes / 不动 saves）；口令「取消确认喜欢」
- 收藏歌手：weapi `artist/sublist` + collector `favorite_artists` + `probe-artists`
- Monitor 最小页：`/monitor/` + `GET /api/music/v1/monitor/snapshot` + CLI `monitor`

## 0.4.0

- QR Cookie 持久化到 Memory Bank credentials；Filesystem Host 写出扫码 QR
- 播放历史：weapi `/weapi/v1/play/record`（需 cryptography）
- 取消收藏：snapshot diff → revoke evidence + supersede saves（不自动撕 likes）
- `MemoryBankBackend` 开放接口（`mnemosyne` / `local_file`）+ 自动 `user_anchor`
- 显式 `confirm-like` / `reject-preference`（CLI + REST + 桌宠桥）
- 一键 `bootstrap`：domain → auth → sync → promote；`Murasame.music_acquisition_bridge`
- 文档 / GA / README → 0.4.0

## 0.3.0

- V2：`RelationKind` / `_RELATION_TYPES` 音乐实例边；授权 sync → domain-tagged Observation outbox；`MusicGraphBuilder`（`saves`/`listened_to`/`contains`，永不自动 likes）
- V3：`PreferenceSignal` 推断信号（evidence_refs；candidate + inferred only）
- CLI：`register-domain`、`submit-graph --promote-graph/--preference-signals/--commit`
- API：`/domain/register`、`/graph/promote`、`/preference/signals`；submit-outbox 支持 promote/prefs
- 契约文档 V2/V3 验收条目

## 0.2.1

- Memory Bank 适配：默认 Host 挂到 `%LOCALAPPDATA%\Mnemosyne Memory Bank\music-acquisition\`
- 歌单边界持久化：`playlist_scopes/` + track↔(源歌单,序号) 往返查找
- 与桌宠 `Murasame.playlist_scope` 双向同步；REST `/playlist/roundtrip|scopes`
- Music Domain Pack V1：安装注册本体（不导入用户偏好）；Observation metadata 契约字段；检索 domain 闸门
- 文档：`docs/MUSIC_DOMAIN_INTEGRATION_CONTRACT.md`

## 0.2.0

- Phase 0–7 闭合：审计文档、Netease HTTP Adapter（config-bind）、Sync Outbox/Recovery、Mnemosyne Evidence/Recall、Filesystem Host、REST `/api/music/v1`、Playback 可选桥接、Legacy music_memory 迁移、GA checklist
- 诚实裁切：QR 登录 / 私人播放历史 / 个性化日推仍标 Unsupported

## 0.1.0

- Phase 1：Engine + HostRuntime + Mock + 契约测试
