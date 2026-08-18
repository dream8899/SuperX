# 安装与 Agent 适配

## 最小依赖

所有路径需要 `Python 3.10+`、`yt-dlp` 和 `ffprobe`。`ffprobe` 来自 FFmpeg，负责
验证，不能省略。Instagram 匿名发现另需 `uv` 或隔离环境中的 `instaloader==4.15.2`。
浏览器、Computer Use、Chrome/CDP、Playwright、Kimi WebBridge 均为可选发现器；它们
不替代 yt-dlp，也不得导出或传递登录会话。

微信视频号单链接可使用普通浏览器下载后由本地注册脚本纳管。批量捕获通常另需微信桌面
客户端、本地代理工具和根证书；这些不是默认依赖，不得由 Agent 静默安装。安装前必须
核验开源仓库和发布包，取得用户对管理员权限、系统代理和证书变更的明确授权，并先写好
清除代理、退出工具和删除根证书的回滚步骤。

| 系统 | Python | yt-dlp | FFmpeg | 可选 uv |
|---|---|---|---|---|
| macOS (Homebrew) | `brew install python` | `brew install yt-dlp` | `brew install ffmpeg` | `brew install uv` |
| Ubuntu/Debian | `sudo apt install python3 python3-venv ffmpeg pipx` | `pipx install -f yt-dlp` | 同左 | `curl -LsSf https://astral.sh/uv/install.sh | sh`（先审阅） |
| Fedora | `sudo dnf install python3 ffmpeg-free pipx` | `pipx install -f yt-dlp` | 同左 | 按 uv 官方安装说明 |
| Arch | `sudo pacman -S python ffmpeg python-pipx yt-dlp` | 同左 | 同左 | `sudo pacman -S uv` |
| Windows (PowerShell) | `winget install Python.Python.3.12` | `winget install yt-dlp.yt-dlp` | `winget install Gyan.FFmpeg` | `winget install astral-sh.uv` |

安装后重新打开终端，运行：

```bash
python3 --version
yt-dlp --version
ffprobe -version
python3 scripts/safe_social_archiver.py doctor --check-updates
```

Windows 可把 `python3` 替换为 `py -3`，把 Unix 风格的 `/absolute/path` 替换成绝对
Windows 路径，例如 `D:\Video_Download\instagram\creator`。所有路径都要加引号。

## Agent 选择与交接

| 环境 | 首选发现路径 | 交接产物 | 禁止项 |
|---|---|---|---|
| CLI Agent | 直接 URL；Instagram 用匿名 Instaloader | `sources.txt` | 伪造浏览器/请求登录 Cookie |
| Codex + Computer Use | 现有浏览器标签页有界滚动 | 规范 URL 或短码 JSON | 用 UI 下载或无限滚动 |
| Chrome/CDP/Playwright | 读取现有页面 DOM 的 `/reel/`、`/p/`、`/shorts/` 链接 | `raw-discovery.txt` | 导出 Cookie、CDN URL 或本地存储 |
| Kimi WebBridge | 只读当前标签/DOM，再规范化 | `raw-discovery.txt` | 猜测桥协议、绑定公网端口、循环 fetch 媒体 |
| 无浏览器控制 | 用户在已登录浏览器复制规范作品链接 | 一行一个 URL 文本 | 索要密码、浏览器配置目录或远程桌面 |

所有 Agent 按同一协议交接：`发现器 → raw-discovery.txt → normalize-discovery →
sources.txt → yt-dlp → ffprobe → metadata/registry`。任何 Agent 都可从 `sources.txt`
继续，无需共享会话。

## 已验证操作路径

### 单个作品

1. 运行 `doctor`，确认 `yt-dlp` 与 `ffprobe` 可用。
2. 用规范作品 URL 调用 `archive --max-items 1`。
3. 仅在 `verified_files=1` 时写入 metadata/registry。

### Instagram 新博主或增量

1. 读取 `metadata.tsv`、归档、文件名和 registry，建立已知短码集合。
2. 匿名发现五条；若失败且用户允许，使用登录浏览器只收集主页公开链接。
3. 规范化、去重、先下载五条、ffprobe 验证。
4. 成功后按最多 20 条串行推进，发现到首个历史短码即停止。
5. 失败时保留 sources、状态、错误分类；冷却后再由下一次明确任务继续。

### TikTok、YouTube 和其他网站

先对直接视频 URL 做一条试下载。只有 `doctor` 显示相关 collection extractor 可用时，
才把主页/频道 URL 作为有界 collection 输入；否则用浏览器或人工收集规范视频 URL。
YouTube Shorts 可使用频道/播放列表原生发现。不要因 Instagram 的限制而放弃其他平台的
原生 yt-dlp 能力。

## 目录迁移与多机器同步

统一采用 `Video_Download/<channel>/<creator>/`。迁移前停止下载，移动整个 creator
目录而非单独 MP4，随后更新 registry 的 `media_dir`。同步时保留 `metadata.tsv`、
`.download-archive.txt`、`batch-log.jsonl`、状态 JSON 与 info JSON；这些文件比序号和
显示作品数更适合作为增量锚点。损坏或后处理失败文件移至 `<channel>/quarantine/`，不删除。
