#!/usr/bin/env python3
"""Adopt a browser/client-downloaded WeChat Channels video into SuperDown88.

This helper never contacts WeChat, reads browser state, or stores temporary CDN URLs.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


FIELDS = ["序号", "发布日期", "短码", "视频地址", "标题", "状态", "文件名", "时长秒", "文件字节", "发现时间", "下载时间", "备注"]
SHARE_RE = re.compile(r"^https://weixin\.qq\.com/sph/([A-Za-z0-9_-]+)(?:[/?#].*)?$")


def share_id(url: str) -> str:
    match = SHARE_RE.match(url.strip())
    if not match:
        raise ValueError("仅接受 https://weixin.qq.com/sph/<ID> 规范分享链接")
    return match.group(1)


def safe_name(value: str, fallback: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\t\r\n]+", "-", value).strip(" .-")
    value = re.sub(r"\s+", "-", value)
    return value[:70] or fallback


def probe(path: Path) -> dict[str, object]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "ffprobe 验证失败")
    data = json.loads(proc.stdout)
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or 0)
    size = int(fmt.get("size") or 0)
    if duration <= 0 or size <= 0 or not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        raise RuntimeError("文件不是可验证的非空视频")
    return {"duration": duration, "size": size, "streams": data.get("streams", [])}


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    rows.sort(key=lambda row: (row.get("发布日期", ""), row.get("短码", "")), reverse=True)
    for index, row in enumerate(rows, 1):
        row["序号"] = f"{index:03d}"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def rebuild_channel(channel: Path) -> None:
    registry = []
    for creator_dir in sorted(p for p in channel.iterdir() if p.is_dir() and not p.name.startswith(".")):
        rows = read_rows(creator_dir / "metadata.tsv")
        media = [p for p in creator_dir.glob("*.mp4") if p.stat().st_size > 0]
        url = next((row.get("视频地址", "") for row in rows if row.get("视频地址")), "")
        registry.append({
            "channel": "wechat_channels", "creator": creator_dir.name,
            "profile_url": url, "media_dir": str(creator_dir),
            "verified_files": len(media), "known_shortcodes": len({r.get("短码", "") for r in rows if r.get("短码")}),
            "last_scanned": datetime.now().date().isoformat(), "status": "active",
            "verification_basis": "ffprobe-on-register",
        })
    fields = ["channel", "creator", "profile_url", "media_dir", "verified_files", "known_shortcodes", "last_scanned", "status", "verification_basis"]
    with (channel / "creator_registry.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader(); writer.writerows(registry)
    (channel / "creator_registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 微信视频号博主注册表", "", f"自动生成时间：{datetime.now().date().isoformat()}", "", "| 渠道 | 博主 | 文件数 | 已知作品 ID | 最近扫描 | 媒体目录 |", "|---|---|---:|---:|---|---|"]
    lines.extend(f"| wechat_channels | {r['creator']} | {r['verified_files']} | {r['known_shortcodes']} | {r['last_scanned']} | `{r['media_dir']}` |" for r in registry)
    (channel / "channel_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def register(args: argparse.Namespace) -> int:
    source = args.downloaded_file.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    media_id = share_id(args.share_url)
    info = probe(source)
    creator = safe_name(args.creator, "unknown-creator")
    channel = args.root.expanduser().resolve() / "wechat_channels"
    creator_dir = channel / creator
    creator_dir.mkdir(parents=True, exist_ok=True)
    metadata = creator_dir / "metadata.tsv"
    rows = read_rows(metadata)
    if any(row.get("短码") == media_id for row in rows):
        print(json.dumps({"status": "already-known", "media_id": media_id, "creator_dir": str(creator_dir)}, ensure_ascii=False))
        return 0
    title = args.title.strip() or media_id
    filename = f"{args.published}_{safe_name(title, media_id)}_{media_id}.mp4"
    target = creator_dir / filename
    if target.exists():
        raise FileExistsError(target)
    shutil.copy2(source, target)
    now = datetime.now(timezone.utc).isoformat()
    rows.append({
        "序号": "", "发布日期": args.published, "短码": media_id,
        "视频地址": f"https://weixin.qq.com/sph/{media_id}", "标题": title,
        "状态": "present", "文件名": filename, "时长秒": f"{info['duration']:.3f}",
        "文件字节": str(info["size"]), "发现时间": now, "下载时间": now,
        "备注": args.note.strip(),
    })
    write_rows(metadata, rows)
    (creator_dir / ".download-archive.txt").open("a", encoding="utf-8").write(f"wechat_channels {media_id}\n")
    (creator_dir / "README.md").write_text(
        f"# {creator}\n\n- 渠道：微信视频号\n- 已验证作品：{len(rows)}\n- 最近更新：{datetime.now().date().isoformat()}\n- 去重主键：规范分享链接中的 `sph` ID\n- 说明：只保存永久分享链接，不保存临时 CDN 地址、Cookie 或登录态。\n",
        encoding="utf-8",
    )
    rebuild_channel(channel)
    print(json.dumps({"status": "registered", "media_id": media_id, "file": str(target), "duration": info["duration"], "size": info["size"]}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    identify = sub.add_parser("identify")
    identify.add_argument("share_url")
    adopt = sub.add_parser("register")
    adopt.add_argument("--share-url", required=True)
    adopt.add_argument("--downloaded-file", type=Path, required=True)
    adopt.add_argument("--root", type=Path, required=True)
    adopt.add_argument("--creator", required=True)
    adopt.add_argument("--title", default="")
    adopt.add_argument("--published", required=True)
    adopt.add_argument("--note", default="")
    args = parser.parse_args()
    if args.command == "identify":
        print(json.dumps({"media_id": share_id(args.share_url), "canonical_url": f"https://weixin.qq.com/sph/{share_id(args.share_url)}"}, ensure_ascii=False))
        return 0
    return register(args)


if __name__ == "__main__":
    raise SystemExit(main())
