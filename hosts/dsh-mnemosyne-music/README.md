# dsh-mnemosyne-music

DSH **Host 桥**：用 Node 包拉起 Python `music_acquisition`（`DshHostRuntime` / Filesystem Host）。

| | |
| --- | --- |
| 做什么 | 提供 `doctor` / `bootstrap` CLI 桥、可选 Cordis `apply` 钩子 |
| 不做什么 | **不**实现采集核心、**不**直连 `memory.db`、**不**假装已是完整 Cordis 市场插件 |

```text
DSH / Node bridge
  → spawn Python music_acquisition.cli
    → DshHostRuntime (HostRuntime)
      → MusicAcquisitionEngine
        → Mnemosyne HTTP
```

## 使用

```bash
# 需要已安装本仓库 Python 包（或 PYTHONPATH=../../src）
npm install
npx dsh-mnemosyne-music doctor
```

环境变量：`MNEMOSYNE_BASE_URL`、`DSH_MEMORY_TOKEN`、`MNEMOSYNE_MUSIC_DATA`（可选）。

## 发布说明

本目录可 `npm publish` 为 **host bridge**。完整 DSH 插件（授权 UI、调度进 DSH 任务树）仍属后续里程碑；当前以 Python/REST 为权威运行面。
