# 故障处理与风控边界

## `sau` 或 Python 不可用

在项目根目录重建项目环境，不要混用系统 Python：

```bash
uv sync --python 3.12
uv run sau tencent --help
```

项目只支持 Python `>=3.10,<3.13`。系统 Python 为 3.13 或更高版本时，明确安装并指定 Python 3.12。

## Patchright 浏览器下载失败

已知 Patchright 1.58.2 在 macOS arm64 上使用 `npmmirror` 时曾找不到 Chromium 构建并返回 404。先移除镜像环境变量，再用官方源：

```bash
unset PLAYWRIGHT_DOWNLOAD_HOST
uv run patchright install chromium
```

Windows PowerShell：

```powershell
Remove-Item Env:PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
uv run patchright install chromium
```

Linux 仅在得到管理员授权后运行 `uv run patchright install-deps chromium` 补齐系统库。

## Cookie 或二维码失败

```bash
uv run sau tencent check --account <account>
uv run sau tencent login --account <account> --headed
```

二维码可能在微信开放平台 iframe 中，旧脚本只查顶层页面会失败。当前 CLI 已处理当前 iframe、可见二维码和相对 `src`；若仍失败，保留有头窗口，检查网络、视频号开通状态与手机端安全确认。二维码、cookie 和截图都不能进入提交或日志。

## 短标题校验

“标题包含特殊字符”通常指 `short_title`。使用 6–16 个中文、英文字母或数字；不要使用连字符、下划线、表情或不受支持标点。仅修复当前未完成条目，不要重新提交已保存的整批任务。

## 草稿保存后仍点击

正确行为是单次点击“保存草稿”或“发表”，然后等待 URL 或成功提示变化。若旧版本仍循环点击，立即停止自动化，检查草稿箱后再决定是否重试；不能在状态未知时重复提交。升级到包含 `--keep-open` 和 `upload-video-batch` 的版本后，批量任务会保持同一窗口供检查。

## 浏览器辅助

- CLI 是批量和可复现的主接口。
- Computer Use、Chrome 控制、Kimi WebBridge 仅用于诊断、用户扫码后确认或一次性页面恢复。
- 浏览器桥接的点击可能是合成事件；验证码、强校验或无法信任的点击应交给用户手动完成，不能绕过平台风控。
- 同一账号一次只运行一个视频号批次；默认草稿，公开发表前再次取得用户确认。
