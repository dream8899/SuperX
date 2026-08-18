#!/usr/bin/env python3
"""Detect and precisely split multi-theme compilation videos."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
from pathlib import Path
from statistics import median
from typing import Any

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}


def run(command: list[str], *, text: bool = True) -> str:
    process = subprocess.run(command, capture_output=True, text=text, check=False)
    if process.returncode:
        error = process.stderr.strip() if text else process.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(error or "command failed")
    return process.stdout if text else process.stdout.decode("utf-8", "replace")


def probe(path: Path) -> dict[str, Any]:
    payload = json.loads(
        run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "stream=index,codec_type,width,height,r_frame_rate,duration:format=duration",
                "-of", "json", str(path),
            ]
        )
    )
    video = next(item for item in payload["streams"] if item["codec_type"] == "video")
    audio = next((item for item in payload["streams"] if item["codec_type"] == "audio"), None)
    video_duration = float(video.get("duration") or payload["format"]["duration"])
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": video["r_frame_rate"],
        "video_duration": video_duration,
        "has_audio": audio is not None,
    }


def scene_scores(path: Path) -> list[tuple[float, float]]:
    output = run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-vf", "select='gte(scene,0)',metadata=print:file=-",
            "-an", "-f", "null", "-",
        ]
    )
    times = re.findall(r"pts_time:([0-9.]+)", output)
    scores = re.findall(r"lavfi\.scene_score=([0-9.]+)", output)
    return [(float(time), float(score)) for time, score in zip(times, scores)]


def effective_duration(path: Path, video_duration: float) -> tuple[float, dict[str, float] | None]:
    process = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
            "-vf", "blackdetect=d=1.0:pix_th=0.10", "-an", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    matches = re.findall(
        r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)",
        process.stderr,
    )
    tails = [
        (float(start), float(end), float(duration))
        for start, end, duration in matches
        if float(end) >= video_duration - 0.2 and float(duration) >= 1.0
    ]
    if not tails:
        return video_duration, None
    start, end, duration = tails[-1]
    return start, {"start": start, "end": end, "duration": duration}


def cadence_result(duration: float, scores: list[tuple[float, float]], cadence: float) -> dict[str, Any]:
    count = max(2, round(duration / cadence))
    step = duration / count
    expected = [step * index for index in range(1, count)]
    boundary_strengths = []
    for target in expected:
        nearby = [score for time, score in scores if abs(time - target) <= 2.5]
        boundary_strengths.append(max(nearby, default=0.0))
    return {
        "cadence": cadence,
        "count": count,
        "step": step,
        "expected": expected,
        "strengths": boundary_strengths,
        "score": round(median(boundary_strengths) + sum(boundary_strengths) / max(1, len(boundary_strengths)), 6),
    }


def choose_boundary(target: float, scores: list[tuple[float, float]]) -> tuple[float, float, str]:
    nearby = [(time, score) for time, score in scores if abs(time - target) <= 2.5]
    if not nearby:
        return target, 0.0, "fallback_expected"
    peak_time, peak_score = max(
        nearby,
        key=lambda item: item[1] - 0.08 * abs(item[0] - target),
    )
    method = "penalized_scene_peak" if peak_score >= 0.18 else "weak_peak_review"
    return round(peak_time, 6), round(peak_score, 6), method


def analyze(path: Path) -> dict[str, Any]:
    media = probe(path)
    usable_duration, black_tail = effective_duration(path, media["video_duration"])
    scores = scene_scores(path)
    candidates = [cadence_result(usable_duration, scores, cadence) for cadence in (10.0, 15.0)]
    ten, fifteen = candidates
    selected = fifteen if fifteen["score"] >= ten["score"] * 0.8 else ten
    boundaries = []
    for target in selected["expected"]:
        actual, strength, method = choose_boundary(target, scores)
        boundaries.append({"expected": round(target, 6), "actual": actual, "strength": strength, "method": method})
    points = [0.0, *(item["actual"] for item in boundaries), usable_duration]
    segments = [
        {
            "index": index + 1,
            "start": round(points[index], 6),
            "end": round(points[index + 1], 6),
            "duration": round(points[index + 1] - points[index], 6),
        }
        for index in range(len(points) - 1)
    ]
    needs_review = any(item["method"] != "penalized_scene_peak" or item["strength"] < 0.18 for item in boundaries)
    return {
        "input": str(path),
        "media": media,
        "usable_duration": usable_duration,
        "ignored_black_tail": black_tail,
        "cadence_candidates": candidates,
        "selected_cadence": selected["cadence"],
        "boundaries": boundaries,
        "segments": segments,
        "status": "needs_review" if needs_review else "ready",
    }


def preview(path: Path, analysis: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    frames: list[Path] = []
    for segment in analysis["segments"]:
        midpoint = (segment["start"] + segment["end"]) / 2
        frame = destination.parent / f".{destination.stem}-{segment['index']:02d}.jpg"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{midpoint:.6f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=240:-2", str(frame)])
        frames.append(frame)
        inputs.extend(["-i", str(frame)])
    columns = min(3, len(frames))
    rows = math.ceil(len(frames) / columns)
    filter_graph = f"tile={columns}x{rows}:padding=4:margin=4"
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", f"{''.join(f'[{i}:v]' for i in range(len(frames)))}concat=n={len(frames)}:v=1:a=0,{filter_graph}", "-frames:v", "1", str(destination)])
    for frame in frames:
        frame.unlink(missing_ok=True)


def safe_label(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value.strip())
    return cleaned.strip("_") or "主题片段"


def split_video(path: Path, analysis: dict[str, Any], output_dir: Path, names: list[str] | None) -> list[dict[str, Any]]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"输出目录非空，拒绝覆盖：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    destinations: list[Path] = []
    for segment in analysis["segments"]:
        index = segment["index"]
        label = safe_label(names[index - 1] if names and index <= len(names) else f"主题片段{index:02d}")
        filename = f"{index:02d}_{label}__{segment['start']:.6f}-{segment['end']:.6f}s.mp4"
        destination = output_dir / filename
        destinations.append(destination)
        results.append({**segment, "name": label, "file": str(destination)})
    count = len(results)
    video_sources = "".join(f"[vsrc{index}]" for index in range(1, count + 1))
    filters = [f"[0:v]split={count}{video_sources}"]
    if analysis["media"]["has_audio"]:
        audio_sources = "".join(f"[asrc{index}]" for index in range(1, count + 1))
        filters.append(f"[0:a]asplit={count}{audio_sources}")
    for item in results:
        index = item["index"]
        filters.append(
            f"[vsrc{index}]trim=start={item['start']}:end={item['end']},setpts=PTS-STARTPTS[v{index}]"
        )
        if analysis["media"]["has_audio"]:
            filters.append(
                f"[asrc{index}]atrim=start={item['start']}:end={item['end']},asetpts=PTS-STARTPTS[a{index}]"
            )
    command = ["ffmpeg", "-hide_banner", "-v", "error", "-n", "-i", str(path), "-filter_complex", ";".join(filters)]
    for item, destination in zip(results, destinations):
        index = item["index"]
        command.extend(["-map", f"[v{index}]"])
        if analysis["media"]["has_audio"]:
            command.extend(["-map", f"[a{index}]"])
        command.extend(["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"])
        if analysis["media"]["has_audio"]:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        command.extend(["-movflags", "+faststart", str(destination)])
    run(command)
    for item, destination in zip(results, destinations):
        run(["ffmpeg", "-v", "error", "-i", str(destination), "-f", "null", "-"])
        actual = probe(destination)
        item["actual_duration"] = actual["video_duration"]
        item["verified"] = True
    manifest = output_dir / "拆分清单.tsv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(results)
    return results


def read_names(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_names_map(path: Path, source: Path) -> list[str] | None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row.get("source_file") == source.name:
                return [item.strip() for item in row.get("names", "").split("|") if item.strip()]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="分析并精准拆分约 10/15 秒多主题拼接视频")
    parser.add_argument("input")
    parser.add_argument("--output-dir")
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--preview")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--names-file")
    parser.add_argument("--names-map", help="TSV：source_file 与以 | 分隔的 names")
    parser.add_argument("--approve-review", action="store_true")
    args = parser.parse_args()
    source = Path(args.input).expanduser().resolve()
    result = analyze(source)
    analysis_path = Path(args.analysis).expanduser().resolve()
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.preview:
        preview(source, result, Path(args.preview).expanduser().resolve())
    outputs = None
    if args.execute:
        if result["status"] == "needs_review" and not args.approve_review:
            raise RuntimeError("边界包含弱切点，必须先审查预览并加入 --approve-review")
        if not args.output_dir:
            parser.error("--execute 需要 --output-dir")
        if args.names_file and args.names_map:
            parser.error("--names-file 与 --names-map 不能同时使用")
        names = (
            read_names(Path(args.names_file).expanduser().resolve())
            if args.names_file
            else read_names_map(Path(args.names_map).expanduser().resolve(), source)
            if args.names_map
            else None
        )
        if names is not None and len(names) != len(result["segments"]):
            raise RuntimeError(f"命名数量 {len(names)} 与片段数量 {len(result['segments'])} 不一致：{source.name}")
        outputs = split_video(
            source,
            result,
            Path(args.output_dir).expanduser().resolve(),
            names,
        )
    print(json.dumps({"status": result["status"], "cadence": result["selected_cadence"], "segments": len(result["segments"]), "analysis": str(analysis_path), "outputs": len(outputs) if outputs else 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
