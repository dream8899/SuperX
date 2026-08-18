#!/usr/bin/env python3
"""Apply approved free-crop candidates and safe refurb transforms to repaired MP4s."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


SPEED = 0.9
UPSCALE = 1.3
ZOOM = 1.1


def run(command: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def probe(path: Path) -> dict:
    result = run(["ffprobe", "-v", "error", "-show_entries",
                  "stream=codec_type,width,height,codec_name:format=duration",
                  "-of", "json", str(path)], timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next(stream for stream in streams if stream.get("codec_type") == "video")
    return {"width": int(video["width"]), "height": int(video["height"]),
            "video_codec": video.get("codec_name"),
            "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
            "duration": float((payload.get("format") or {}).get("duration") or 0.0)}


def verify_decode(path: Path) -> None:
    result = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-1000:] or "output decode failed")


def even(value: int, minimum: int = 2) -> int:
    return max(minimum, value // 2 * 2)


def even_coord(value: int) -> int:
    """Normalize a crop origin without turning a valid zero into two pixels."""
    return max(0, value // 2 * 2)


def crop_rect(item: dict, width: int, height: int) -> tuple[int, int, int, int, str]:
    crop = item.get("crop") or {}
    has_candidate = bool(crop.get("accepted_candidates"))
    # A detector may retain a low-confidence diagnostic rectangle, but it must
    # never affect execution unless a candidate passed the acceptance gate.
    rect = (crop.get("crop_rect") if has_candidate else None) or {
        "x": 0, "y": 0, "width": width, "height": height
    }
    x, y = even_coord(int(rect.get("x", 0))), even_coord(int(rect.get("y", 0)))
    w, h = even(int(rect.get("width", width))), even(int(rect.get("height", height)))
    x = min(x, even(width - 2))
    y = min(y, even(height - 2))
    w = min(w, even(width - x))
    h = min(h, even(height - y))
    return x, y, w, h, "candidate_crop" if has_candidate else "no_reliable_target_crop"


def target_for(source: Path, target_dir: Path) -> Path:
    # Explicit files supplied from the refurb directory are their own targets;
    # this also disambiguates variants such as 030 and 030_precise.
    if source.parent == target_dir and source.is_file():
        return source
    number = source.name.split("__", 1)[0]
    matches = sorted(path for path in target_dir.glob(f"{number}*.mp4") if path.is_file())
    if len(matches) != 1:
        raise RuntimeError(f"无法唯一匹配损坏成品：{source.name} -> {[item.name for item in matches]}")
    return matches[0]


def process_item(item: dict, target_dir: Path, output_dir: Path) -> dict:
    source = Path(item["input"]).expanduser().resolve()
    if item.get("status") == "unplayable":
        raise RuntimeError(f"源文件仍不可播放：{source}")
    source_media = probe(source)
    target = target_for(source, target_dir)
    output = output_dir / target.name
    if output.exists():
        raise RuntimeError(f"输出已存在：{output}")
    x, y, w, h, crop_status = crop_rect(item, source_media["width"], source_media["height"])
    filters = [
        f"crop={w}:{h}:{x}:{y}",
        f"scale=ceil(iw*{UPSCALE * ZOOM:g}/2)*2:ceil(ih*{UPSCALE * ZOOM:g}/2)*2:flags=lanczos",
        f"crop=trunc(iw/{ZOOM:g}/2)*2:trunc(ih/{ZOOM:g}/2)*2:(iw-ow)/2:(ih-oh)/2",
        "hqdn3d=1.0:0.75:1.5:1.125",
        "eq=contrast=1.03:saturation=1.03:brightness=0:gamma=1",
        "unsharp=5:5:0.35:5:5:0",
        f"setpts=(PTS-STARTPTS)/{SPEED:g}",
    ]
    graph = [f"[0:v:0]{','.join(filters)}[v]"]
    command = ["ffmpeg", "-hide_banner", "-v", "error", "-n", "-i", str(source),
               "-filter_complex", ";".join(graph), "-map", "[v]"]
    if source_media["has_audio"]:
        graph.append(f"[0:a:0]asetpts=PTS-STARTPTS,atempo={SPEED:g}[a]")
        command[command.index("-filter_complex") + 1] = ";".join(graph)
        command.extend(["-map", "[a]"])
    command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)])
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run(command, timeout=1200)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-1200:] or "ffmpeg remix failed")
    verify_decode(output)
    output_media = probe(output)
    if output_media["video_codec"] != "h264" or (source_media["has_audio"] and not output_media["has_audio"]):
        raise RuntimeError(f"输出轨道验证失败：{output_media}")
    return {"source": str(source), "target": str(target), "output": str(output),
            "status": "verified", "crop_status": crop_status, "crop_rect": {"x": x, "y": y, "width": w, "height": h},
            "parameters": {"upscale": UPSCALE, "zoom": ZOOM, "speed": SPEED,
                            "color": "natural", "denoise": "light", "sharpen": "light",
                            "filter_order": ["crop", "upscale", "zoom", "denoise", "color", "sharpen", "av_speed"]},
            "source_media": source_media, "output_media": output_media}


def main() -> int:
    parser = argparse.ArgumentParser(description="对已验证源文件执行自由裁剪和翻新，并在完成验证后替换损坏成品")
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--replace-targets", action="store_true")
    args = parser.parse_args()
    analysis = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    target_dir, output_dir, backup_dir = map(lambda value: Path(value).expanduser().resolve(),
                                              (args.target_dir, args.output_dir, args.backup_dir))
    results, failures = [], []
    for item in analysis:
        try:
            results.append(process_item(item, target_dir, output_dir))
        except Exception as exc:
            failures.append({"input": item.get("input"), "status": "failed", "error": str(exc)})
    if failures:
        report = {"status": "blocked", "verified_count": len(results), "failures": failures, "results": results}
        (output_dir / "repair_execution.json").parent.mkdir(parents=True, exist_ok=True)
        (output_dir / "repair_execution.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    if args.replace_targets:
        backup_dir.mkdir(parents=True, exist_ok=False)
        for result in results:
            target = Path(result["target"])
            shutil.move(str(target), str(backup_dir / target.name))
        for result in results:
            Path(result["output"]).replace(Path(result["target"]))
    report = {"status": "replaced" if args.replace_targets else "verified_ready",
              "verified_count": len(results), "failures": [], "results": results,
              "backup_dir": str(backup_dir) if args.replace_targets else None}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "repair_execution.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
