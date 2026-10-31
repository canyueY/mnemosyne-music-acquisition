# Mnemosyne Music Acquisition — GA / Hardening Checklist

**组件版本：** 0.5.0  
**日期：** 2026-10-31  
**总体：** **Beta+**（P0/P1 闭合；仍非「全平台 GA」）

## 必须绿

- [x] Phase 0 审计四件套文档  
- [x] HostRuntime / SourceAdapter 契约 + Mock 测试  
- [x] Scope 默认关闭  
- [x] partial 不覆盖 complete  
- [x] 推荐曝光不写长期喜欢  
- [x] Netease HTTP Adapter（config-bind + QR）  
- [x] Snapshot hash / diff / TTL + 取消收藏 revoke  
- [x] Sync pending + startup recovery hooks  
- [x] Evidence Outbox + Mnemosyne HTTP submitter  
- [x] Music Domain Pack 注册客户端（inline pack → `POST /api/v1/domains/register`；缺路由诚实失败）  
- [x] Graph promote（saves / listened_to；永不自动 likes）  
- [x] PreferenceSignal（inferred）  
- [x] confirmed / likes **显式** confirm API + CLI + 桌宠桥  
- [x] `MemoryBankBackend` 开放接口（mnemosyne / local_file）  
- [x] 自动 user_anchor  
- [x] 一键 bootstrap（domain → auth → sync → promote；**domain_ok 计入 ok**）  
- [x] Filesystem Host（Memory Bank）  
- [x] REST `/api/music/v1`（可选 `[http]`）  
- [x] Playback / playlist_scope 可选桥接桌宠  
- [x] Legacy `music_memory` 只读迁移  
- [x] **Search 契约**：`POST /api/v1/memories/search` + `domain` + 客户端二次过滤  
- [x] 契约单测（假 HTTP；非 live Node）  

## 明确黄 / 裁切（不假装完成）

- [x] 收藏歌手（weapi `artist/sublist`；需 Cookie；`probe-artists` 验收）  
- [x] DSH Host 桥：`DshHostRuntime` + `hosts/dsh-mnemosyne-music` npm 包（Python bridge；非完整 Cordis 市场插件）  
- [x] 播放后 CDP `verifyPlayback`（`cdp_is_playing` / track id 轮询）  
- [x] Monitor 最小页（`/monitor/` + `monitor` CLI）  
- [x] confirmed likes 显式取消（`unconfirm-like`；不动 saves）  
- [x] Cookie at-rest 加密 / OS keyring（可选）+ 本地 master key  
- [x] 组件内真实 scheduler（`ThreadingScheduler` + `enable-auto-sync`）  
- [x] Outbox 逐条确认 + 文件锁 + 退避  
- [x] Claim 映射层（原生 Claims API 或 Memory fallback）；recall 快照 `claims` 仍非 Claim 对象  
- [x] 对真实 Node 的可选契约测试（env 门控）  
- [ ] Node 侧检索闸门 `require_domain_or_music_intent` 完整策略执行（依赖 Node；客户端已传 domain + 二次过滤）  
- [ ] Cookie 自动轮换 / credentialRefresh（网易云 Unsupported）  
- [ ] 多音乐源（仍仅网易云）  


## 运行验收

```bat
set PYTHONPATH=src
py -3 -m unittest discover -s tests -q
py -3 -m music_acquisition.cli doctor
py -3 -m music_acquisition.cli bootstrap --playlist-id <红心歌单ID>
py -3 -m music_acquisition.cli confirm-like --target-id <曲目ID> --dry-run
```
