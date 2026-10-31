# MUSIC_MIGRATION_MAP

**规则：** Mnemosyne 是唯一长期音乐认知权威；禁止 `music_memory` 与 Mnemosyne 双写权威。

| 旧模块簇 | 目标归属 | 动作 |
| --- | --- | --- |
| `netease_music` 网页拉取（playlist/daily/song detail） | `source/netease` Adapter | **Wrap** 为 HTTP 实现（可无 Murasame import） |
| `netease_music` 播放 / URI / CDP 调度 | `playback` 可选子模块 | **Wrap**；Capability 声明 |
| `netease_music` 对话意图 / LLM JSON | 宿主 Agent 层 | **Retain** 在桌宠；不进入 Engine |
| `music_library` 快照文件 | Acquisition Snapshot Cache | **Wrap** → SnapshotManager 格式；可读旧 JSON |
| `music_library` 日调度 | SyncCoordinator trigger=scheduled | **Replace** 调度入口 |
| `music_memory` profile/events | Legacy Memory Migration Adapter | **Migrate-read** → Evidence；停止新 LT 权威写入 |
| `music_memory` inject/recall 文本块 | Music Recall（过渡） | 兼容读取；新路径走 Mnemosyne recall |
| `music_recognition` | 旁路 | **Retain**；非 Acquisition 核心 |

## 切换顺序

1. Engine + Snapshot 并行写入新 Bank 缓存  
2. Evidence Outbox → Mnemosyne  
3. `music_memory` 改为只读迁移 / 停止 seed 为权威  
4. 稳定后再考虑删除旧写入路径  

## 明确禁止

- 新功能继续把「长期喜欢」只写进 `data/music_memory/profile.json`  
- Engine `import` DSH 内部模块作为硬依赖（可选 bridge 允许）  
