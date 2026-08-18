#!/usr/bin/env python3
"""按已审核计划从原视频重建误拆目录，并保留可恢复备份。"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


def run(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def has_audio(path: Path) -> bool:
    result = run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
    ])
    return result.returncode == 0 and bool(result.stdout.strip())


def read_segments(manifest: Path) -> list[dict]:
    with manifest.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise ValueError(f"空清单: {manifest}")
    return [
        {
            "start": float(row["start"]),
            "end": float(row["end"]),
            "duration": float(row["end"]) - float(row["start"]),
        }
        for row in rows
    ]


def rebuild_segments(segments: list[dict], remove_boundaries: list[int]) -> list[dict]:
    invalid = [index for index in remove_boundaries if index < 1 or index >= len(segments)]
    if invalid:
        raise ValueError(f"边界编号超出范围: {invalid}; 段数={len(segments)}")
    removed = set(remove_boundaries)
    points = [segments[0]["start"]]
    points.extend(segments[index - 1]["end"] for index in range(1, len(segments)) if index not in removed)
    points.append(segments[-1]["end"])
    return [
        {"index": index + 1, "start": points[index], "end": points[index + 1],
         "duration": points[index + 1] - points[index]}
        for index in range(len(points) - 1)
    ]


def encode_segment(source: Path, segment: dict, destination: Path, audio: bool) -> None:
    command = [
        "ffmpeg", "-hide_banner", "-v", "error", "-y",
        "-ss", f"{segment['start']:.6f}", "-t", f"{segment['duration']:.6f}",
        "-i", str(source), "-map", "0:v:0",
    ]
    if audio:
        command.extend(["-map", "0:a:0"])
    command.extend([
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p",
    ])
    if audio:
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    command.extend(["-movflags", "+faststart", str(destination)])
    result = run(command)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-1000:])


def verify(path: Path) -> dict:
    decode = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
    probe = run([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,codec_name:format=duration", "-of", "json", str(path),
    ])
    if decode.returncode or probe.returncode:
        raise RuntimeError((decode.stderr or probe.stderr).strip()[-1000:])
    payload = json.loads(probe.stdout)
    video = next((item for item in payload["streams"] if item["codec_type"] == "video"), None)
    audio = next((item for item in payload["streams"] if item["codec_type"] == "audio"), None)
    if not video or video["codec_name"] != "h264":
        raise RuntimeError(f"视频编码验证失败: {path}")
    return {
        "full_decode": True,
        "duration": float(payload["format"]["duration"]),
        "video_codec": video["codec_name"],
        "audio_codec": audio["codec_name"] if audio else None,
    }


def write_manifest(path: Path, segments: list[dict], files: list[Path]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["index", "start", "end", "duration", "file", "verified"],
            delimiter="\t",
        )
        writer.writeheader()
        for segment, file in zip(segments, files):
            writer.writerow({
                "index": segment["index"],
                "start": f"{segment['start']:.3f}",
                "end": f"{segment['end']:.3f}",
                "duration": f"{segment['duration']:.3f}",
                "file": file.name,
                "verified": True,
            })


def repair_one(
    split_root: Path,
    source_root: Path,
    backup_root: Path,
    directory_name: str,
    remove_boundaries: list[int],
) -> dict:
    directory = split_root / directory_name
    manifest = directory / "拆分清单.tsv"
    source = source_root / f"{directory_name}.mp4"
    if not manifest.is_file() or not source.is_file():
        raise FileNotFoundError(f"缺少拆分清单或原视频: {directory_name}")

    old_segments = read_segments(manifest)
    new_segments = rebuild_segments(old_segments, remove_boundaries)
    audio = has_audio(source)
    with tempfile.TemporaryDirectory(prefix=".false-split-repair-", dir=split_root) as staging_name:
        staging = Path(staging_name)
        staged_files = []
        verifications = []
        for segment in new_segments:
            destination = staging / (
                f"{segment['index']:02d}_t{segment['start']:.1f}-{segment['end']:.1f}s.mp4"
            )
            encode_segment(source, segment, destination, audio)
            verifications.append(verify(destination))
            staged_files.append(destination)
        staged_manifest = staging / "拆分清单.tsv"
        write_manifest(staged_manifest, new_segments, staged_files)

        backup = backup_root / directory_name
        backup.mkdir(parents=True, exist_ok=False)
        old_files = sorted(directory.glob("[0-9][0-9]_t*.mp4"))
        for old in old_files:
            shutil.move(str(old), backup / old.name)
        shutil.move(str(manifest), backup / manifest.name)
        for staged in staged_files:
            shutil.move(str(staged), directory / staged.name)
        shutil.move(str(staged_manifest), directory / staged_manifest.name)

    return {
        "directory": directory_name,
        "source": str(source),
        "removed_boundary_indices": remove_boundaries,
        "old_segment_count": len(old_segments),
        "new_segment_count": len(new_segments),
        "new_segments": new_segments,
        "verification": verifications,
        "backup": str(backup),
        "status": "repaired",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="重建经人工审核确认的误拆目录")
    parser.add_argument("--split-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    items = plan.get("repairs", [])
    if not args.execute:
        print(json.dumps({"status": "plan_only", "repair_count": len(items)}, ensure_ascii=False))
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = args.split_root / f"_误拆修复备份_{stamp}"
    backup_root.mkdir(parents=True, exist_ok=False)
    results = []
    try:
        for item in items:
            results.append(repair_one(
                args.split_root,
                args.source_root,
                backup_root,
                item["directory"],
                [int(value) for value in item["remove_boundary_indices"]],
            ))
    except Exception as error:
        report = {
            "status": "failed",
            "error": str(error),
            "completed": results,
            "backup_root": str(backup_root),
        }
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise

    report = {
        "status": "ok",
        "completed_at": datetime.now().astimezone().isoformat(),
        "repair_count": len(results),
        "backup_root": str(backup_root),
        "results": results,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
