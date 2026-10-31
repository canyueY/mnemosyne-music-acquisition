# 最终验收记录（2026-11-01）

组件：`mnemosyne-music-acquisition`  
目标：完成最终离线验收、DSH 桥验证、独立运行包构建与脱离 Python 环境验证。

## 1. 结论

代码层与离线验收通过；最终 Windows 运行包已重建并可脱离 Python 启动。

仍未完成的只有**真实外部环境验证**：

- 真实网易云账号扫码登录；
- 真实网易云接口的写操作与频控；
- 真实网易云客户端播放核验。

这些不能由临时数据库或 Mock 代替。

## 2. 测试与桥接

### Python

```text
py -3 -m pytest tests -q
62 passed, 2 skipped
```

跳过项：

```text
tests/test_live_mnemosyne_contract.py
  live Node env not configured
```

说明：

- 跳过的是“外部 live Node 环境”门控测试，不是失败；
- `tests/test_isolated_node_integration.py` 已使用临时 SQLite Bank 实际启动兄弟 Mnemosyne REST app，验证提交证据、确认、撤销、再次确认和分离删除；
- 新增 `tzdata` 依赖，修复 Python 3.13 在 Windows 上 `ZoneInfo("Asia/Shanghai")` 缺失导致 6 项失败的问题。

### DSH 桥

```text
node --test test_bridge.cjs
2 passed, 0 failed
```

修复内容：

- `business-tools.json` 缺少 `music_sync_status`，从 9 项补到 10 项；
- 已同步更新：
  - `src/music_acquisition/resources/business-tools.json`
  - `hosts/dsh-mnemosyne-music/business-tools.json`
  - 构建产物内的同名文件。

## 3. 运行包

构建：

```text
py -3 scripts/build_standalone.py --output release\windows
```

产物：

```text
release/windows/mnemosyne-music/mnemosyne-music.exe
release/windows/dsh-mnemosyne-music/
```

PyInstaller：`6.22.3`  
入口：`launch.py`  
构建包含：

- `uvicorn` / `qrcode` / `tzdata` / `keyring.backends`；
- `PIL.Image` / `cryptography.fernet`；
- Monitor、Domain Pack、组件 Manifest；
- DSH 桥及运行时副本。

## 4. 脱离 Python 环境验证

在清空 `PYTHONHOME` / `PYTHONPATH`，并将 `PATH` 限制为：

```text
C:\Windows\System32;C:\Windows
```

后执行：

```text
mnemosyne-music.exe --data-dir <临时目录> doctor
```

结果：

```text
exit=0
engine_shutdown
```

说明：

- 可执行文件不依赖系统 Python；
- 默认 Memory Bank 路径可能被现有进程或 ACL 锁定，验收时使用 `--data-dir` 指向临时目录；
- 这是数据路径隔离，不是 Python 依赖。

## 5. 仍需实机验证

| 项 | 状态 |
|---|---|
| 真实二维码登录 | 未验证 |
| 真实网易云接口写操作 | 未验证 |
| 真实客户端播放核验（CDP / 当前曲目） | 未验证 |
| 真实账号频控与限流等待 | 未验证 |
| 真实长期运行 scheduler | 未验证 |

建议实机顺序：

1. `mnemosyne-music configure` 配置 owner/scopes/Consent；
2. `mnemosyne-music probe-artists` 验证 Cookie；
3. 用小号/测试歌单执行 `sync-netease`；
4. 验证 `monitor`；
5. 最后验证播放与 `verifyPlayback`。

## 6. 回滚

运行包：

```text
release/windows/
```

可整体删除后重新构建；源码不变。

数据：

- 验收使用 `--data-dir` 临时目录；
- 未写入真实网易云账号；
- 未对生产 Memory Bank 做迁移。
