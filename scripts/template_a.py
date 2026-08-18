#!/usr/bin/env python3
"""Prepare and optionally run a WeChat Channels Template A draft batch."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".webm"}
DEFAULT_DESC = "分享本期视频内容，欢迎关注。"


def split_tags(value: str) -> list[str]:
    return [tag.strip().lstrip("#") for tag in value.split(",") if tag.strip()]


def simple_title(filename: Path, index: int) -> str:
    value = re.sub(r"[_-]+", " ", filename.stem)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:30] or f"本期视频分享{index}"


def simple_short_title(title: str, index: int) -> str:
    value = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", title)[:16]
    if len(value) >= 6:
        return value
    return f"本期视频分享{index}"[:16]


def load_copy(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not all(isinstance(value, dict) for value in raw.values()):
        raise ValueError("--copy-json 必须是以视频文件名为 key、文案对象为 value 的 JSON 对象")
    return raw


def infer_catalog_root(directory: Path) -> Path | None:
    for candidate in (directory, *directory.parents):
        if candidate.name == "Video_Download":
            return candidate.resolve()
    return None


def resolve_catalog_cli(explicit: Path | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("SUPER_MEDIA_CATALOG_CLI"):
        candidates.append(Path(os.environ["SUPER_MEDIA_CATALOG_CLI"]))
    candidates.extend(
        [
            Path.home() / ".codex/skills/superdown88/scripts/media_asset_catalog.py",
            Path.home() / ".agents/skills/superdown88/scripts/media_asset_catalog.py",
        ]
    )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file():
            return resolved
    raise ValueError("未找到 superdown88 的 media_asset_catalog.py；请传 --catalog-cli")


def run_catalog(
    catalog_cli: Path,
    catalog_root: Path,
    action: str,
    manifest_path: Path,
    account: str,
    *,
    allow_source_repeat: bool = False,
    status: str | None = None,
    verification: str | None = None,
) -> subprocess.CompletedProcess:
    command = [
        sys.executable,
        str(catalog_cli),
        "--root",
        str(catalog_root),
        action,
        "--manifest",
        str(manifest_path),
        "--target-platform",
        "tencent",
        "--target-account",
        account,
    ]
    if allow_source_repeat and action == "reserve-manifest":
        command.append("--allow-source-repeat")
    if status is not None:
        command.extend(["--status", status])
    if verification is not None:
        command.extend(["--verification", verification])
    return subprocess.run(command, capture_output=True, text=True, check=False)


def build_manifest(
    directory: Path,
    copy_by_name: dict[str, dict],
    default_desc: str,
    default_tags: list[str],
    include_names: set[str] | None = None,
) -> list[dict]:
    videos = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS)
    if include_names:
        available_names = {video.name for video in videos}
        missing_names = sorted(include_names - available_names)
        if missing_names:
            raise ValueError(f"--include 未在目录中找到: {', '.join(missing_names)}")
        videos = [video for video in videos if video.name in include_names]
    if not videos:
        raise ValueError(f"目录中没有支持的视频文件: {directory}")

    manifest: list[dict] = []
    for index, video in enumerate(videos, start=1):
        copy = copy_by_name.get(video.name, {})
        title = str(copy.get("title") or simple_title(video, index)).strip()
        desc = str(copy.get("desc") or default_desc).strip()
        raw_tags = copy.get("tags") if copy.get("tags") is not None else default_tags
        tags = split_tags(raw_tags) if isinstance(raw_tags, str) else [str(tag).strip().lstrip("#") for tag in raw_tags if str(tag).strip()]
        manifest.append(
            {
                "file": str(video.resolve()),
                "title": title,
                "desc": desc,
                "tags": tags,
                "short_title": str(copy.get("short_title") or simple_short_title(title, index)),
                "draft": True,
            }
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="视频号模板 A：目录批量保存草稿")
    parser.add_argument("--directory", required=True, type=Path, help="视频目录")
    parser.add_argument("--account", required=True, help="sau 中的视频号账号名")
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd(), help="social-auto-upload 项目根目录")
    parser.add_argument("--copy-json", type=Path, help="按文件名提供 title/desc/tags/short_title 的 JSON")
    parser.add_argument(
        "--include",
        action="append",
        help="仅加入此文件名；可重复传入，适合先测试一条素材",
    )
    parser.add_argument("--desc", default=DEFAULT_DESC, help="没有指定文案时使用的默认文案")
    parser.add_argument("--tags", default="", help="没有指定标签时使用的逗号分隔标签")
    parser.add_argument("--delay-min-seconds", type=int, default=10)
    parser.add_argument("--delay-max-seconds", type=int, default=20)
    parser.add_argument("--manifest", type=Path, help="生成的批量清单路径")
    parser.add_argument("--catalog-root", type=Path, help="Video_Download 根目录；默认从素材路径推断")
    parser.add_argument("--catalog-cli", type=Path, help="media_asset_catalog.py 路径")
    parser.add_argument(
        "--allow-source-repeat",
        action="store_true",
        help="明确允许同一源作品的不同 Remix 进入同一账号；完全相同成品仍会阻止",
    )
    parser.add_argument("--execute", action="store_true", help="登录预检通过后实际保存草稿")
    args = parser.parse_args()

    directory = args.directory.expanduser().resolve()
    repo_dir = args.repo_dir.expanduser().resolve()
    if not directory.is_dir():
        parser.error(f"视频目录不存在: {directory}")
    if not (repo_dir / "pyproject.toml").is_file():
        parser.error(f"不是 social-auto-upload 项目根目录: {repo_dir}")
    if args.delay_min_seconds < 0 or args.delay_max_seconds < args.delay_min_seconds:
        parser.error("间隔必须满足 0 <= --delay-min-seconds <= --delay-max-seconds")

    copy_by_name = load_copy(args.copy_json.expanduser().resolve() if args.copy_json else None)
    manifest = build_manifest(
        directory,
        copy_by_name,
        args.desc,
        split_tags(args.tags),
        set(args.include) if args.include else None,
    )
    manifest_path = args.manifest or directory / f"template-a-{datetime.now():%Y%m%d-%H%M%S}.json"
    manifest_path = manifest_path.expanduser().resolve()
    if manifest_path.exists():
        parser.error(f"清单已存在，请指定新的 --manifest: {manifest_path}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {len(manifest)} 条草稿清单: {manifest_path}")

    catalog_root = (
        args.catalog_root.expanduser().resolve()
        if args.catalog_root
        else infer_catalog_root(directory)
    )
    if catalog_root is None:
        parser.error("无法从素材目录推断 Video_Download；请传 --catalog-root")
    catalog_cli = resolve_catalog_cli(args.catalog_cli)
    preflight = run_catalog(
        catalog_cli,
        catalog_root,
        "preflight-manifest",
        manifest_path,
        args.account,
    )
    if preflight.returncode == 4:
        print(preflight.stdout, end="")
        print("统一资产账本发现阻止项，未启动上传。", file=sys.stderr)
        return 4
    if preflight.returncode == 3 and not args.allow_source_repeat:
        print(preflight.stdout, end="")
        print("存在同源或跨账号提醒；确认后追加 --allow-source-repeat。", file=sys.stderr)
        return 3
    if preflight.returncode not in {0, 3}:
        print(preflight.stderr or preflight.stdout, file=sys.stderr)
        return preflight.returncode

    if not args.execute:
        print(preflight.stdout, end="")
        print("预演完成。确认清单后追加 --execute 执行预约、登录预检和草稿上传。")
        return 0

    check = subprocess.run(["uv", "run", "sau", "tencent", "check", "--account", args.account], cwd=repo_dir)
    if check.returncode:
        print(f"账号 {args.account} 的 sau 登录态无效；请先执行 headed 登录，未启动上传。", file=sys.stderr)
        return check.returncode

    reservation = run_catalog(
        catalog_cli,
        catalog_root,
        "reserve-manifest",
        manifest_path,
        args.account,
        allow_source_repeat=args.allow_source_repeat,
    )
    if reservation.returncode:
        print(reservation.stderr or reservation.stdout, file=sys.stderr)
        return reservation.returncode

    command = [
        "uv", "run", "sau", "tencent", "upload-video-batch",
        "--account", args.account,
        "--manifest", str(manifest_path),
        "--delay-min-seconds", str(args.delay_min_seconds),
        "--delay-max-seconds", str(args.delay_max_seconds),
        "--return-home",
    ]
    print("登录预检通过；将使用单一可见 Chrome 窗口顺序保存草稿。")
    print("任务完成后窗口保持打开；不会自动定时刷新或模拟在线操作。")
    upload = subprocess.run(command, cwd=repo_dir)
    status = "draft_saved_verified" if upload.returncode == 0 else "status_unknown"
    verification = "draft_box_title" if upload.returncode == 0 else "batch_interrupted_requires_audit"
    completion = run_catalog(
        catalog_cli,
        catalog_root,
        "complete-manifest",
        manifest_path,
        args.account,
        status=status,
        verification=verification,
    )
    if completion.returncode:
        print(completion.stderr or completion.stdout, file=sys.stderr)
        return completion.returncode
    return upload.returncode


if __name__ == "__main__":
    raise SystemExit(main())
