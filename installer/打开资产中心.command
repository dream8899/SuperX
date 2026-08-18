#!/usr/bin/env bash
# SuperX 资产中心一键启动（macOS）
# 已运行则直接打开浏览器；未运行则后台拉起服务（关闭终端不影响服务）。

SUPERX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="http://127.0.0.1:8765"
LOG="/tmp/superx-console.log"

if curl -s -m 2 -o /dev/null "$URL"; then
  open "$URL"
  echo "资产中心已在运行，已打开浏览器。"
else
  python3 - "$SUPERX_DIR" "$LOG" <<'PY'
import subprocess
import sys

skill_dir, log_path = sys.argv[1], sys.argv[2]
log = open(log_path, "ab")
subprocess.Popen(
    [sys.executable, skill_dir + "/superx.py", "console", "serve", "--port", "8765", "--open"],
    cwd=skill_dir,
    stdout=log,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
PY
  sleep 1
  open "$URL"
  echo "已启动资产中心：$URL（日志 $LOG）"
fi
