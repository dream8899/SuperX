# 跨平台安装与依赖

以下路径已经在 macOS Apple Silicon 上用 Python 3.12、Patchright 1.58.2 和系统 Google Chrome 验证；Windows、Linux 使用同一 CLI 契约，但应先完成各自的 Chrome 与 Python/uv 安装。

## 通用前置条件

- Python `>=3.10,<3.13`；推荐 3.12。
- `uv` 用于创建虚拟环境并安装项目。
- Google Chrome（稳定版）。视频号默认通过 Chrome channel 启动一个独立窗口，不会复用用户已打开的标签页。
- 网络可访问微信、平台后台和 Python/PyPI 依赖源。

不要把 `requirements.txt` 当作主安装入口；以 `pyproject.toml` 为准。新环境优先 `uv sync --python 3.12`；需要把 `sau` 安装进已激活环境时，再使用 `uv pip install -e .`。

## macOS（zsh/bash）

```bash
cd <repo-root>
uv python install 3.12
uv sync --python 3.12
cp conf.example.py conf.py
uv run patchright install chromium
uv run sau --help
uv run sau tencent --help
```

Chrome 通常会被自动发现。若机器装在非标准位置，在 `conf.py` 中设置 `LOCAL_CHROME_PATH`，例如 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`。

## Windows（PowerShell）

```powershell
Set-Location <repo-root>
uv python install 3.12
uv sync --python 3.12
Copy-Item conf.example.py conf.py
uv run patchright install chromium
uv run sau --help
uv run sau tencent --help
```

若 Chrome 没有被自动发现，在 `conf.py` 设置 `LOCAL_CHROME_PATH`，常见值是 `C:/Program Files/Google/Chrome/Application/chrome.exe`。PowerShell 的多行续行使用反引号 `` ` ``，不是 bash 的 `\`。

## Linux（bash）

```bash
cd <repo-root>
uv python install 3.12
uv sync --python 3.12
cp conf.example.py conf.py
uv run patchright install chromium
uv run sau --help
uv run sau tencent --help
```

在带桌面的 Linux 上，先安装 Google Chrome 并确保当前 agent 能创建可见窗口。纯服务器没有可交互扫码能力时，不要把“无头登录”作为默认方案；应在受信任的有桌面环境完成扫码，再仅在得到用户许可时迁移账号状态。若 Patchright 报缺少系统库，获得管理员授权后执行 `uv run patchright install-deps chromium`，再重试浏览器安装。

## Patchright 浏览器下载

项目使用 `patchright`，但视频号默认请求系统 Google Chrome；安装下面的内置 Chromium **不能替代** Chrome。只有其他流程或自定义启动方式明确需要 Patchright Chromium 时，才在已激活的虚拟环境中执行：

```bash
patchright install chromium
```

曾出现过 `npmmirror` 未同步 Patchright 1.58.2 macOS arm64 构建、返回 404 的情况。遇到镜像 404 时，移除 `PLAYWRIGHT_DOWNLOAD_HOST` 后重试官方源；不要无限重试同一个镜像。只有确认当前构建在镜像中存在时，才把镜像设为下载源。

## 最小验收

```bash
uv run sau --help
uv run sau tencent --help
uv run sau tencent check --account <account>
```

`check` 返回 `valid` 后才执行上传。不要把 `cookies/`、`conf.py`、`.venv/`、二维码图片或平台调试截图加入 Git。
