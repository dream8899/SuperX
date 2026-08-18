#!/usr/bin/env bash
set -euo pipefail

# SuperX 一键安装（macOS）
# 下载/混剪：系统 Python 3.10+ + FFmpeg + yt-dlp + uv
# 上传：独立 3.12 虚拟环境安装 sau（social-auto-upload）

SUPERX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAU_REPO="${SAU_REPO:-/Users/solo/Desktop/AI工作室/social-auto-upload}"

if ! command -v brew >/dev/null 2>&1; then
  echo "==> 安装 Homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

echo "==> 安装基础依赖（python / ffmpeg / git / yt-dlp / uv）"
brew install python ffmpeg git yt-dlp uv

if [ -d "$SAU_REPO" ]; then
  echo "==> 配置上传环境（Python 3.12 + sau + patchright chromium）"
  cd "$SAU_REPO"
  uv python install 3.12
  uv sync --python 3.12
  if [ ! -f conf.py ]; then cp conf.example.py conf.py; fi
  uv run patchright install chromium
else
  echo "==> 未找到 sau 仓库：$SAU_REPO（可 export SAU_REPO=/你的/social-auto-upload 后重跑）"
fi

echo "==> 自检"
python3 "$SUPERX_DIR/superx.py" doctor
echo "==> 完成。用法：python3 \"$SUPERX_DIR/superx.py\" <download|mix|upload|ledger|console>"
