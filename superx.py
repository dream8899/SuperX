#!/usr/bin/env python3
"""SuperX — 统一入口：下载 / 混剪 / 上传 / 账本 / 资产中心。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
SCRIPTS = SKILL_DIR / "scripts"
SAU_REPO = Path(os.environ.get("SUPERX_SAU_REPO", "/Users/solo/Desktop/AI工作室/social-auto-upload"))
VIDEO_DOWNLOAD_ROOT = Path(os.environ.get("SUPERX_VIDEO_ROOT", "/Users/solo/Desktop/AI工作室/Video_Download"))
VIDEOHUB_ROOT = Path(os.environ.get("SUPERX_VIDEOHUB_ROOT", "/Users/solo/Desktop/AI工作室/VideoHub"))


def found_accounts() -> dict[str, list[str]]:
    """只返回账号名/路径，不读取任何 cookie 或 conf 值。"""
    result: dict[str, list[str]] = {"tencent_profiles": [], "tencent_cookies": [], "jimeng_books": []}
    if SAU_REPO.exists():
        profiles_dir = SAU_REPO / "profiles" / "tencent"
        if profiles_dir.is_dir():
            result["tencent_profiles"] = sorted(p.name for p in profiles_dir.iterdir() if p.is_dir())
        cookies_dir = SAU_REPO / "cookies" / "tencent_uploader"
        if cookies_dir.is_dir():
            result["tencent_cookies"] = sorted(p.name for p in cookies_dir.iterdir() if not p.name.startswith("."))
    if VIDEOHUB_ROOT.is_dir():
        for book in sorted(VIDEOHUB_ROOT.rglob("_ACCOUNT_BOOK.csv")):
            result["jimeng_books"].append(str(book))
    return result


def _run(script: str, args: list[str], cwd: Path | None = None) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPTS) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    return subprocess.call(cmd, cwd=cwd or SKILL_DIR, env=env)


def _have(cmd: str) -> str | None:
    return shutil.which(cmd)


def cmd_doctor(_args: list[str]) -> int:
    chrome = _have("google-chrome") or _have("Google Chrome") or _have("chrome")
    if not chrome:
        for candidate in (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Google Chrome.app"),
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        ):
            if candidate.exists():
                chrome = str(candidate)
                break
    rows = [
        ("python3", _have("python3") or _have("py")),
        ("ffmpeg", _have("ffmpeg")),
        ("ffprobe", _have("ffprobe")),
        ("yt-dlp", _have("yt-dlp")),
        ("uv", _have("uv")),
        ("chrome", chrome),
        ("sau repo", str(SAU_REPO if SAU_REPO.exists() else "") or None),
        ("Video_Download", str(VIDEO_DOWNLOAD_ROOT if VIDEO_DOWNLOAD_ROOT.exists() else "") or None),
    ]
    ok = True
    for name, value in rows:
        mark = "OK" if value else "MISSING"
        if not value:
            ok = False
        print(f"[{mark:7}] {name:12} {value or ''}")
    accounts = found_accounts()
    tencent_accounts = sorted(set(accounts["tencent_profiles"]) | {f"cookie:{c}" for c in accounts["tencent_cookies"]})
    print(f"[INFO   ] 视频号账号 {len(tencent_accounts)} 个: {', '.join(tencent_accounts) or '无'}")
    print(f"[INFO   ] 即梦账本 {len(accounts['jimeng_books'])} 个: {', '.join(accounts['jimeng_books']) or '无'}")
    print()
    if _have("yt-dlp"):
        subprocess.call([_have("yt-dlp"), "--version"])
    return 0 if ok else 1


def cmd_accounts(_args: list[str]) -> int:
    accounts = found_accounts()
    tencent_accounts = sorted(set(accounts["tencent_profiles"]) | {f"cookie:{c}" for c in accounts["tencent_cookies"]})
    print("== 视频号（tencent）账号（继承自 sau 仓库，无需重新扫码）")
    for name in tencent_accounts:
        print(f"  - {name}")
    print("== 即梦（Jimeng）工作区账本")
    for book in accounts["jimeng_books"]:
        print(f"  - {book}")
    print("== 路径")
    print(f"  sau 仓库: {SAU_REPO}")
    print(f"  视频号持久 Profile: {SAU_REPO / 'profiles' / 'tencent'}")
    print(f"  即梦工作区根: {VIDEOHUB_ROOT}")
    return 0


def cmd_download(args: list[str]) -> int:
    return _run("safe_social_archiver.py", args)


def cmd_mix(args: list[str]) -> int:
    return _run("video_pipeline.py", args)


def cmd_ledger(args: list[str]) -> int:
    return _run("media_asset_catalog.py", ["--root", str(VIDEO_DOWNLOAD_ROOT), *args])


def cmd_console(args: list[str]) -> int:
    return _run("supermedia_console.py", ["--root", str(VIDEO_DOWNLOAD_ROOT), *args])


def cmd_upload(args: list[str]) -> int:
    # 模板 A：目录一键草稿（调用 superdown88 账本门禁 + sau）
    if args and args[0] == "template-a":
        return _run("template_a.py", args[1:])
    if not SAU_REPO.exists():
        print(f"未找到 sau 仓库：{SAU_REPO}", file=sys.stderr)
        return 2
    cmd = ["uv", "run", "--project", str(SAU_REPO), "sau", *args]
    return subprocess.call(cmd, cwd=SAU_REPO)


def cmd_learn(args: list[str]) -> int:
    area = "learnings"
    summary = ""
    details = ""
    tags: list[str] = []
    source = "task_run"
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--area" and i + 1 < len(args):
            area = args[i + 1]
            i += 2
        elif arg == "--summary" and i + 1 < len(args):
            summary = args[i + 1]
            i += 2
        elif arg == "--details" and i + 1 < len(args):
            details = args[i + 1]
            i += 2
        elif arg == "--tags" and i + 1 < len(args):
            tags = [t.strip() for t in args[i + 1].split(",") if t.strip()]
            i += 2
        elif arg == "--source" and i + 1 < len(args):
            source = args[i + 1]
            i += 2
        else:
            print(f"未知参数: {arg}", file=sys.stderr)
            return 2
    if not summary:
        print("--summary 必填", file=sys.stderr)
        return 2
    if area not in ("learnings", "errors"):
        print("--area 只能是 learnings 或 errors", file=sys.stderr)
        return 2
    target = SKILL_DIR / ".learnings" / ("LEARNINGS.md" if area == "learnings" else "ERRORS.md")
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    code = "LRN" if area == "learnings" else "ERR"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    seq = 1
    for match in re.finditer(rf"\[{code}-(\d{{8}})-(\d{{3}})\]", existing):
        if match.group(1) == stamp.split("-")[0]:
            seq = max(seq, int(match.group(2)) + 1)
    entry_id = f"{code}-{stamp.split('-')[0]}-{seq:03d}"
    now = datetime.now().astimezone().isoformat()
    lines = [
        f"## [{entry_id}] {summary[:60]}",
        "",
        f"**Logged**: {now}",
        "**Priority**: medium",
        "**Status**: candidate",
        f"**Area**: {area}",
        "",
        "### Summary",
        summary,
    ]
    if details:
        lines += ["", "### Details", details]
    lines += [
        "",
        "### Metadata",
        f"- Source: {source}",
        "- Related Files: （待补充）",
        f"- Tags: {', '.join(tags) if tags else '（待补充）'}",
        f"- Pattern-Key: {entry_id.lower()}",
        "",
    ]
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"已追加候选 {entry_id} -> {target.relative_to(SKILL_DIR)}")
    return 0


USAGE = """SuperX 统一入口

用法:
  superx doctor
  superx accounts
  superx learn --area learnings|errors --summary "..." [--details ...] [--tags a,b] [--source ...]
  superx download <safe_social_archiver 参数...>
  superx mix <video_pipeline 参数...>
  superx upload <platform ...> | template-a <参数...>
  superx ledger <media_asset_catalog 参数...>
  superx console <supermedia_console 参数...>
"""


def main() -> int:
    if len(sys.argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    sub = sys.argv[1]
    args = sys.argv[2:]
    handlers = {
        "doctor": cmd_doctor,
        "accounts": cmd_accounts,
        "learn": cmd_learn,
        "download": cmd_download,
        "mix": cmd_mix,
        "upload": cmd_upload,
        "ledger": cmd_ledger,
        "console": cmd_console,
    }
    handler = handlers.get(sub)
    if handler is None:
        print(USAGE, file=sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
