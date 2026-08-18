$ErrorActionPreference = 'Stop'

# SuperX 一键安装（Windows / PowerShell）
# 下载/混剪：Python 3.10+ + FFmpeg + yt-dlp + uv
# 上传：独立 3.12 虚拟环境安装 sau（social-auto-upload）

$SUPERX = Split-Path -Parent $PSScriptRoot
$SAU = $env:SAU_REPO
if (-not $SAU) {
  $SAU = Read-Host "请输入 social-auto-upload 仓库路径（例如 D:\AI工作室\social-auto-upload）"
}

Write-Host "==> 安装基础依赖（winget）"
winget install --id Python.Python.3.12 -e --silent
winget install --id Gyan.FFmpeg -e --silent
winget install --id Git.Git -e --silent
winget install --id yt-dlp.yt-dlp -e --silent
winget install --id astral-sh.uv -e --silent

if (Test-Path $SAU) {
  Write-Host "==> 配置上传环境（Python 3.12 + sau + patchright chromium）"
  Set-Location $SAU
  uv python install 3.12
  uv sync --python 3.12
  if (-not (Test-Path conf.py)) { Copy-Item conf.example.py conf.py }
  uv run patchright install chromium
  Set-Location $SUPERX
} else {
  Write-Host "==> 未找到 sau 仓库：$SAU"
}

Write-Host "==> 自检"
python "$SUPERX\superx.py" doctor
Write-Host "==> 完成。用法：python `"$SUPERX\superx.py`" <download|mix|upload|ledger|console>"
