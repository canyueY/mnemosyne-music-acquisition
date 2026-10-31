# dsh-mnemosyne-music（Host Adapter 占位）

DSH 插件壳：只实现 `HostRuntime`，加载 `music_acquisition` Engine。

**不要**在此实现采集核心或长期画像。

```text
DSH Plugin
  → HostRuntime (credential / storage / UI)
  → MusicAcquisitionEngine
  → Mnemosyne HTTP (Evidence)
```

正式 npm 发布另开里程碑；当前以 Python 包 + REST 为准。
