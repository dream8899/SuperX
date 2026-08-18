# 跨 Agent 与 CLI 复现指南

## 目录

- 稳定交接协议
- 能力检测与选择
- 纯 CLI 路径
- 通用浏览器发现器约束
- Kimi WebBridge 与扩展桥
- 无浏览器控制能力时
- 可复现记录

目标是让任意 Agent 只要能运行命令，或能从浏览器复制链接，就能完成同一套安全归档。
不要把 Computer Use、Kimi WebBridge 或任何 MCP 当成必需依赖。

## 稳定交接协议

把流程拆为三个互不依赖的阶段：

```text
发现器 → 一行一个规范 URL 的 sources.txt → yt-dlp 归档器 → ffprobe/状态文档
```

发现器只需产出公开作品 URL，或 Instagram 短码 JSON：

```json
{"codes":["SHORTCODE1","SHORTCODE2"]}
```

不要让发现器下载媒体、导出 Cookie、返回临时 CDN URL，或直接改 `metadata.tsv`。
临时 CDN URL 会过期；规范作品 URL 和媒体 ID 才是长期主键。

## 能力检测与选择

按顺序选择第一个可用档位：

| 档位 | 所需能力 | 用途 | 注意事项 |
|---|---|---|---|
| A | Shell + yt-dlp | 单链接或已验证支持的原生频道/playlist | 首选，无浏览器 |
| B | Shell + Instaloader | 匿名、有限发现 Instagram Reel URL | 只发现，不下载媒体 |
| C | CDP、Playwright/MCP、Chrome 控制或 Computer Use | 有界滚动并收集作品 URL | 仅发现，不抓媒体流 |
| D | Kimi WebBridge 或同类扩展桥 | 复用用户已登录的现有标签页 | 仅在用户授权且桥已安装时使用 |
| E | 第三方公开解析站 | 单链接人工验证 | 不作为批量依赖，不调用未公开 API |
| F | 微信桌面客户端 + 本地捕获工具 | 视频号批量、公开分享页无媒体流 | 系统代理/根证书必须显式批准并可回滚 |

先运行依赖检查：

```bash
python3 scripts/safe_social_archiver.py doctor --check-updates
```

## 纯 CLI 路径

Instagram 匿名发现试批；已有 metadata 时，遇到第一个已知短码即停止：

```bash
uv run --with 'instaloader==4.15.2' python3 scripts/safe_social_archiver.py \
  discover-instagram CREATOR --known '/ABS/Video_Download/instagram/CREATOR/metadata.tsv' \
  --max-items 5 --output sources.txt
python3 scripts/safe_social_archiver.py archive --sources-file sources.txt \
  --output-dir '/ABS/Video_Download/instagram/CREATOR' --max-items 5
```

如果另一个 Agent、浏览器扩展或人工操作产生了 `raw-discovery.txt`：

```bash
python3 scripts/safe_social_archiver.py normalize-discovery \
  raw-discovery.txt --instagram-profile CREATOR --output sources.txt
python3 scripts/safe_social_archiver.py archive \
  --sources-file sources.txt --source-offset 0 \
  --output-dir '/ABS/Video_Download/instagram/CREATOR' --max-items 5
```

`raw-discovery.txt` 可以是自然语言、HTML、JSON、完整 URL，或在明确提供
`--instagram-profile` 时使用的一行一个短码。规范化命令会清除跟踪参数并去重。
首批验证成功后，把 `--source-offset` 递增 5，并将 `--max-items` 提高到最多 20；
任何失败都不要递增 offset。文件输入始终只取当前切片，不会一次提交整份清单。

## 通用浏览器发现器约束

无论使用 Computer Use、Chrome Control、浏览器 MCP、Playwright/CDP 还是扩展桥：

1. 复用用户指定的现有窗口/标签页；只有用户明确允许时才新建会话。
2. 读取当前 URL，确认账号主页正确，再开始有限滚动。
3. 以滚动主页为主：读取当前页面已有 `/reel/`、`/p/`、`/tv/` href，
   每次只滚动一个视口，随机等待 3–8 秒后再次收集链接；每轮只返回新增 URL。
4. 连续两轮无新增、出现公开分页/内容末尾或达到任务上限即停止。弹窗“下一步”
   仅允许最多 5 条补漏，不得用快速连续点击替代滚动。
5. 保存原始发现结果后立即交给规范化脚本；后续下载不再反复访问主页。
6. 遇到 429、challenge、checkpoint、验证码、超时或页面空白立即停止，不刷新恢复。

即使发现器使用登录页面，也只能输出规范作品 URL/短码。禁止向下载脚本传递 Cookie、
session file、localStorage、Authorization 头或临时 CDN URL。

浏览器工具若只能截图和点击，也可以逐页复制链接；准确性优先于自动化程度。

## Kimi WebBridge 与扩展桥

把扩展桥视作一种 UI 传输层，而不是下载器。先探测它实际暴露的工具名称和参数，
不要假定不同版本都支持相同协议。优先使用读取标签页、获取 DOM/页面文本、执行只读
脚本等能力，一次返回一批规范作品 URL。

- 只连接 `127.0.0.1`/本机 Unix socket，不监听局域网或公网地址。
- 不把账号 Cookie、localStorage、Authorization 头或页面完整会话数据写入日志。
- 不注入循环 `fetch` 下载媒体，不轮询每个作品的临时 CDN 地址。
- 不复制项目中临时编写的 WebSocket daemon 作为通用默认实现；协议不稳定且可能无认证。
- 桥断开或工具调用超时时保留已发现 URL，切回人工复制或其他浏览器控制工具。

## 无浏览器控制能力时

CLI Agent 应输出一条明确的人工交接指令：让用户在现有浏览器中复制作品链接或导出
页面链接到文本文件。收到文件后，从 `normalize-discovery` 继续；不要要求密码、Cookie、
浏览器配置目录或远程桌面权限。

微信视频号例外：当前 yt-dlp 不支持 `weixin.qq.com/sph/`。CLI Agent 应先运行一次
公开无 Cookie 探测；失败后让用户在浏览器单条解析并下载本地文件，随后调用
`scripts/wechat_channels_register.py` 纳管。完整路径见
[wechat-channels.md](wechat-channels.md)。

## 可复现记录

每轮在账号 README 或批次日志记录：Agent/工具名称与版本、发现档位、原始发现文件、
规范 URL 数、唯一媒体 ID 数、试批大小、yt-dlp 版本、成功/失败分类和停止原因。
不要记录会话密钥。另一个 Agent 应仅凭这些文件和本 Skill 接续任务。

## 本机副本同步

Skill 维护者提交更新后，先在当前仓库运行：

```bash
python3 scripts/sync_local_installs.py
```

脚本扫描 `~/.codex/skills` 与 `~/.agents/skills` 中已存在且 frontmatter 为
`name: superdown88` 的副本，只复制变更文件，不删除目标内容。其他 Agent 根目录用
重复的 `--root` 指定；没有发现目标时表示当前机器只有一个安装副本。完成本机同步后
再推送 GitHub，避免出现“远端已更新但本机 Agent 仍使用旧 Skill”的分叉。

## 已验证的 Instagram 归档复盘

在大型已有归档上先执行本地盘点，再访问主页：

```bash
python3 Video_Download/reconcile_instagram_metadata.py --root Video_Download/instagram
python3 Video_Download/update_creator_registry.py --root Video_Download/instagram
```

盘点只读取每个博主目录顶层 MP4，生成含规范地址的 12 列 metadata；短码识别会处理
账号名前缀和 `__h264-aac`/`__fdash` 后缀，metadata 按日期倒序。`--verify` 是显式慢速
ffprobe 模式。随后把 metadata 作为 `--known` 输入，遇到首个已知短码停止；这条路径已
验证可避免重复下载，并能被 CLI、Codex、Chrome/CDP、Computer Use 和 Kimi WebBridge
分别复现。
