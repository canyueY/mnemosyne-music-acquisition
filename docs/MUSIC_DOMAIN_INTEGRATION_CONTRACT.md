# Music Domain Integration Contract

**状态：** V1–V3 已落地（本体 + 检索闸门 + 授权同步 Observation/实例边 + PreferenceSignal）。  
**规则：** 安装 ≠ 授权 ≠ 同步。安装只扩展 Mnemosyne 领域知识结构。永不静默 likes/confirmed。

## 1. 职责划分

| 侧 | 职责 |
| --- | --- |
| 音乐插件 `mnemosyne-music-acquisition` | 授权、拉取、标准化、snapshot、outbox → Observation |
| Mnemosyne Node | Domain Pack、Observation/Evidence/Memory 生命周期、检索闸门 |
| Memory Bank | `%LOCALAPPDATA%\Mnemosyne Memory Bank`；插件数据在 `music-acquisition/` |

禁止：插件直连 `memory.db`；安装时写入用户偏好 Memory。

## 2. 本体映射（V1）

| Pack 概念 | 落点 |
| --- | --- |
| `domain:music` + Track/Artist/… | `CognitiveSchema`（`store_cognitive_schema`） |
| 概念间 `network_links` | schema payload `network_links` |
| 关系类型声明 | root schema `relations_declared`（声明；实例边见 V2） |
| 复用 User/Time/Event/Source | pack `reuse_concepts`；不创建 MusicUser |

Pack 文件：[`domain/music_domain_pack.v1.json`](../domain/music_domain_pack.v1.json)

注册 API：

- `POST /api/v1/domains/register` — `{ "pack_path" }` 或 `{ "pack": {...} }` 或排空 pending
- `GET /api/v1/domains/{domain_id}` — 读 `domain:{id}` schema
- 组件安装：manifest `domain_packs` → pending → install 响应里 `domain_pack_registration`

## 3. 事实 / 行为 / 推断（三层）

| 层 | `data_kind` | `status` | 可否升格长期 likes |
| --- | --- | --- | --- |
| 来源行为 | `user_action` / `catalog` | `observed` | 否（自动） |
| 偏好信号 | `preference_signal` | `inferred` | 否 |
| 用户确认偏好 | `preference` | `confirmed` | 是（显式反馈） |

Observation `metadata` 约定字段：

```json
{
  "domain": "music",
  "source_type": "external_app",
  "source_app": "netease",
  "source_record_id": "...",
  "data_kind": "user_action",
  "action": "saved",
  "target_type": "track",
  "target_id": "...",
  "occurred_at": "...",
  "imported_at": "...",
  "status": "observed",
  "evidence_refs": []
}
```

映射到现有 [`ObservationIn`](../../mnemosyne/src/cognitive_memory/domain/models.py)：`source_kind=system`，正文可读摘要，结构化进 `metadata`。

## 4. 检索闸门（选择算法关联）

- `SearchQuery.domain` / `ContextBuildIn.domain`
- `domain` 空且 query 无音乐意图 → **排除** `metadata.domain=music`
- `domain=music` 或音乐意图 → 保留并提升 music 片段
- 实现：[`domains/retrieval.py`](../../mnemosyne/src/cognitive_memory/domains/retrieval.py)

插件 catalog（playlist_scopes / snapshots）经 recall 注入时必须带 `data_kind=catalog`，不得标成 likes。

## 5. 安装 / 授权 / 同步

1. **安装**：拷贝组件 + 幂等注册 Music Domain（ontology only）
2. **授权**：`component authorize` + 音乐 scopes / 账号绑定
3. **同步**：`sync_now` → evidence outbox → `observe`（V2）

重复安装：schema id 相同则 reuse，不重复造概念；不兼容则 ChangePlan / PROPOSED，不静默覆盖。

## 6. 验收（V1）

| 场景 | 预期 |
| --- | --- |
| 注册 pack | 创建 `domain:music` 与子概念 |
| 再注册 | reused，无冲突爆炸 |
| 无关 query 检索 | 不含 music 域记忆 |
| 音乐意图检索 | 可含 music 域记忆 |
| 安装 | 不产生用户收藏 Memory |

## 7. V2 — 实例边与授权同步

允许自动写入的 **observed** 边（`RelationKind` / `_RELATION_TYPES`）：

`contains` · `performed_by` · `composed_by` · `belongs_to` · `tagged_as` · `listened_to` · `saves` · `follows` · `imported_from`

**禁止自动：** `likes` / `favorites`（仅用户确认后）。

同步路径：

1. `sync_now`（须已授权 + scopes）→ snapshot → `MusicMemoryAdapter`（`domain=music`，`status=observed`）
2. outbox → `POST /observations`（metadata 契约字段）
3. 可选 promote：`MusicGraphBuilder` → candidate Memory + `saves` / `listened_to` / `contains`（需 `user_anchor` 才能挂 User 端点）

插件 CLI / API：

- `register-domain` · `POST /api/music/v1/domain/register`
- `submit-graph --promote-graph [--preference-signals] [--commit]`
- `POST /api/music/v1/graph/promote` · `…/admin/submit-outbox?promote_graph=true`

## 8. V3 — PreferenceSignal（推断 only）

- `build_preference_signals`：聚合 saved/played → `status=inferred`，带 `evidence_refs`
- 写入 Memory 时强制 `candidate` + `epistemic=inference` + `must_not_write_likes`
- `POST /api/music/v1/preference/signals`（默认 dry_run）
- 升格 `confirmed` / `likes` **仅**显式用户反馈：
  - CLI `confirm-like` / REST `POST /api/music/v1/preference/confirm`
  - 桌宠 `Murasame.music_acquisition_bridge.confirm_track_like`
  - 同步路径 **禁止** 调用

### Memory Bank 开放接口

```text
MemoryBankBackend  (ABC)
  ├─ MnemosyneBankBackend   # 默认：Service Contract HTTP
  └─ LocalFileBankBackend   # 备选：Bank 内 JSON 镜像 / 离线 / 他库适配样板
```

工厂：`open_memory_bank(kind="mnemosyne"|"local_file"|"dual", …)`  
运行时审计落在 `<Memory Bank>/music-acquisition/memory_bank_mirror/`。  
其它记忆库：实现同一 ABC 即可替换，无需改采集核心。

`ensure_user_anchor()` 幂等创建/复用 User 端点 Memory，promote / likes 自动使用。

## 9. 验收（V2/V3）

| 场景 | 预期 |
| --- | --- |
| saved → graph | `saves`，无 `likes` |
| played → graph | `listened_to`，无 `likes` |
| recommendation_seen | catalog only，不进 preference Memory |
| PreferenceSignal | `inferred` + evidence_refs；不写 likes |
| 无关检索 | 仍排除 music（V1 闸门） |
