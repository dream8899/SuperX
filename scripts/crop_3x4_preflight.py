#!/usr/bin/env python3
"""Batch preflight for top-anchored 9:16 to 3:4 cropping."""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def run(command: list[str]) -> bytes:
    process = subprocess.run(command, capture_output=True, check=False)
    if process.returncode:
        raise RuntimeError(process.stderr.decode("utf-8", "replace").strip() or "command failed")
    return process.stdout


def probe(path: Path) -> dict[str, Any]:
    payload = json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    stream = payload["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": float(payload.get("format", {}).get("duration") or 0),
    }


def discover(value: Path) -> list[Path]:
    if value.is_file():
        return [value.resolve()]
    return sorted(
        path.resolve()
        for path in value.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    )


def frame_height(width: int, source_width: int, source_height: int) -> int:
    return max(2, round(source_height * width / source_width / 2) * 2)


def mean_edge(frame: bytes, width: int, height: int, y0: int, y1: int) -> float:
    total = 0
    count = 0
    for y in range(y0, y1):
        row = y * width
        for x in range(width - 1):
            total += abs(frame[row + x + 1] - frame[row + x])
            count += 1
        if y + 1 < y1:
            next_row = row + width
            for x in range(width):
                total += abs(frame[next_row + x] - frame[row + x])
                count += 1
    return total / max(1, count)


def mean_motion(previous: bytes, current: bytes, width: int, y0: int, y1: int) -> float:
    start = y0 * width
    end = y1 * width
    return sum(abs(current[index] - previous[index]) for index in range(start, end)) / max(1, end - start)


def sample_metrics(path: Path, media: dict[str, Any], samples: int) -> dict[str, float]:
    width = 180
    height = frame_height(width, media["width"], media["height"])
    fps = max(0.1, samples / max(media["duration"], 1.0))
    raw = run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={fps:.8f},scale={width}:{height}:flags=area,format=gray",
            "-frames:v",
            str(samples),
            "-f",
            "rawvideo",
            "-",
        ]
    )
    frame_size = width * height
    frames = [raw[offset : offset + frame_size] for offset in range(0, len(raw) - frame_size + 1, frame_size)]
    if not frames:
        raise RuntimeError("无法抽取分析帧")
    keep_height = min(height, round((width / (3 / 4)) / 2) * 2)
    if keep_height >= height:
        return {"edge_ratio": 0.0, "motion_ratio": 0.0, "content_score": 0.0}
    keep_edges = [mean_edge(frame, width, height, keep_height // 3, keep_height) for frame in frames]
    band_edges = [mean_edge(frame, width, height, keep_height, height) for frame in frames]
    keep_motion: list[float] = []
    band_motion: list[float] = []
    for previous, current in zip(frames, frames[1:]):
        keep_motion.append(mean_motion(previous, current, width, keep_height // 3, keep_height))
        band_motion.append(mean_motion(previous, current, width, keep_height, height))
    edge_ratio = (sum(band_edges) / len(band_edges)) / max(0.1, sum(keep_edges) / len(keep_edges))
    motion_ratio = (
        (sum(band_motion) / len(band_motion)) / max(0.1, sum(keep_motion) / len(keep_motion))
        if band_motion
        else 0.0
    )
    score = 0.58 * min(edge_ratio, 2.0) + 0.42 * min(motion_ratio, 2.0)
    return {
        "edge_ratio": round(edge_ratio, 3),
        "motion_ratio": round(motion_ratio, 3),
        "content_score": round(score, 3),
    }


def preview(path: Path, media: dict[str, Any], output: Path, samples: int) -> None:
    width = 360
    height = frame_height(width, media["width"], media["height"])
    keep_height = min(height - 2, round((width / (3 / 4)) / 2) * 2)
    fps = max(0.1, samples / max(media["duration"], 1.0))
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(path),
            "-vf",
            (
                f"fps={fps:.8f},scale={width}:{height}:flags=lanczos,"
                f"drawbox=x=0:y={keep_height}:w=iw:h=4:color=red:t=fill,"
                f"drawbox=x=0:y={keep_height}:w=iw:h=ih-{keep_height}:color=red@0.12:t=fill,"
                f"tile=3x{math.ceil(samples / 3)}"
            ),
            "-frames:v",
            "1",
            str(output),
        ]
    )


def classify(media: dict[str, Any], metrics: dict[str, float]) -> tuple[str, str]:
    ratio = media["width"] / media["height"]
    if not 0.52 <= ratio <= 0.61:
        return "不适合", f"输入画幅 {media['width']}x{media['height']} 不是标准 9:16，暂停自动裁切"
    score = metrics["content_score"]
    if score <= 0.60 and metrics["edge_ratio"] <= 0.65 and metrics["motion_ratio"] <= 0.75:
        return "适合", "底部裁除带的细节与运动显著低于主体区域，可优先预览"
    if score <= 1.05:
        return "人工复核", "底部裁除带存在一定内容，检查车轮、手部、字幕或产品细节"
    return "不适合", "底部裁除带内容活跃，顶部对齐 3:4 可能明显误裁主体"


def main() -> int:
    parser = argparse.ArgumentParser(description="评估 9:16 顶部对齐裁成 3:4 的内容安全性")
    parser.add_argument("input")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument("--start-date", default=date.today().isoformat())
    parser.add_argument("--per-day", type=int, default=10)
    args = parser.parse_args()
    if not 3 <= args.samples <= 12:
        parser.error("--samples 必须在 3–12 之间")
    if not 1 <= args.per_day <= 100:
        parser.error("--per-day 必须在 1–100 之间")
    start = date.fromisoformat(args.start_date)
    output_dir = Path(args.output_dir).expanduser().resolve()
    preview_dir = output_dir / "previews"
    rows: list[dict[str, Any]] = []
    for path in discover(Path(args.input).expanduser().resolve()):
        media = probe(path)
        metrics = sample_metrics(path, media, args.samples)
        decision, reason = classify(media, metrics)
        preview_path = preview_dir / f"{path.stem}__crop3x4-top-preview.jpg"
        preview(path, media, preview_path, args.samples)
        rows.append(
            {
                "视频文件": str(path),
                "分辨率": f"{media['width']}x{media['height']}",
                "时长秒": round(media["duration"], 3),
                "裁除比例": round(max(0.0, 1 - (media["width"] / (3 / 4)) / media["height"]), 3),
                "底部细节比": metrics["edge_ratio"],
                "底部运动比": metrics["motion_ratio"],
                "内容风险分": metrics["content_score"],
                "筛选结论": decision,
                "意见": reason,
                "预览图": str(preview_path),
            }
        )
    order = {"适合": 0, "人工复核": 1, "不适合": 2}
    rows.sort(key=lambda row: (order[row["筛选结论"]], row["内容风险分"], row["视频文件"]))
    ready_index = 0
    for index, row in enumerate(rows, 1):
        row["优先级"] = f"P{order[row['筛选结论']] + 1}"
        if row["筛选结论"] == "不适合":
            row["建议排期"] = "HOLD"
        else:
            row["建议排期"] = (start + timedelta(days=ready_index // args.per_day)).isoformat()
            ready_index += 1
        row["顺序"] = index
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    tsv_path = output_dir / "crop_3x4_screening_schedule.tsv"
    with tsv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "kind": "crop-3x4-top-preflight",
        "input": str(Path(args.input).expanduser().resolve()),
        "policy": "先筛选和人工预览，批准后才生成裁切计划",
        "counts": {label: sum(row["筛选结论"] == label for row in rows) for label in order},
        "items": rows,
    }
    json_path = output_dir / "crop_3x4_screening_schedule.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "tsv": str(tsv_path), "json": str(json_path), **summary["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
