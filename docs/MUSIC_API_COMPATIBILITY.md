# MUSIC_API_COMPATIBILITY

## 网页端（现网代码已用）

| URL | 用途 | 鉴权 | 备注 |
| --- | --- | --- | --- |
| `https://music.163.com/api/v6/playlist/detail` | 歌单曲目 | 公开 Cookie 头 `os=pc` 级即可读多数歌单 | `trackIds` 需二次 `song/detail` |
| `https://music.163.com/api/v3/song/detail` | 批量曲目元数据 | 同上 | 分页 200 |
| `https://music.163.com/api/v3/discovery/recommend/songs` | 每日推荐 | **无需登录** | 返回通用日推池，非账号个性化 |

## 本地工具

| 工具 | 用途 | 合规/产品约束 |
| --- | --- | --- |
| 桌面网易云 + CDP 9222 | 播放 / 当前曲 | 可选；用户已有客户端 |
| ncm-cli | 登录检查 / encrypted-id | **不可**作为零工具硬依赖 |

## 失败模式

| 现象 | 处理 |
| --- | --- |
| 歌单 detail 仅返回少量 tracks | 用 trackIds + song/detail 补全 |
| 补取失败 | partial；不覆盖完整快照 |
| 未配置 favorite_playlist_id | likedSongs → unsupported/failed + 明确错误码 |
| CDP 未开 | playbackControl=false；采集仍可进行 |
| 社区 NeteaseCloudMusicApi | 仅研究参考；本 Adapter **不**默认依赖公共实例存 Cookie |
