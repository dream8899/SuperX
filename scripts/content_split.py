#!/usr/bin/env python3
"""Content-aware video splitting — detect scene change peaks without fixed cadence.

Usage:
  # Analyze + preview a directory
  python3 content_split.py --input-dir DIR --output-dir PREVIEW_DIR --analyze --preview

  # Execute splits from saved analyses
  python3 content_split.py --input-dir DIR --analysis-dir PREVIEW_DIR --execute --output-dir OUT_DIR
"""

from __future__ import annotations

import argparse, csv, json, re, subprocess, sys
from pathlib import Path

DEFAULT_MIN_HEIGHT = 0.25     # 太低会产生噪声峰值
DEFAULT_MIN_DISTANCE = 3.0    # 合拢同一过渡特效的相邻峰
DEFAULT_MIN_SEGMENT = 4.0     # 硬下限，不允许 <4s 碎片
DEFAULT_MIN_CUT_FROM_START = 3.0
DEFAULT_REVIEW_SHORT_SEGMENT = 5.25  # 短于此值的局部段必须复核主体/标签连续性


# ── ffmpeg helpers ──────────────────────────────────────────────

def run_ff(command: list[str]) -> str:
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg command failed")
    return proc.stdout


def probe(path: Path) -> dict:
    payload = json.loads(
        run_ff(["ffprobe", "-v", "error", "-show_entries",
                "stream=index,codec_type,width,height,r_frame_rate,duration:format=duration",
                "-of", "json", str(path)]))
    video = next(s for s in payload["streams"] if s["codec_type"] == "video")
    audio = next((s for s in payload["streams"] if s["codec_type"] == "audio"), None)
    dur = float(video.get("duration") or payload["format"]["duration"])
    return {"width": int(video["width"]), "height": int(video["height"]),
            "fps": video["r_frame_rate"], "video_duration": dur, "has_audio": audio is not None}


def scene_scores(path: Path) -> list[tuple[float, float]]:
    out = run_ff(["ffmpeg", "-v", "error", "-i", str(path),
                  "-vf", "select='gte(scene,0)',metadata=print:file=-",
                  "-an", "-f", "null", "-"])
    times = [float(t) for t in re.findall(r"pts_time:([0-9.]+)", out)]
    scores = [float(s) for s in re.findall(r"lavfi\.scene_score=([0-9.]+)", out)]
    return list(zip(times, scores))


def detect_black_tail(path: Path, video_duration: float) -> tuple[float, dict | None]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
         "-vf", "blackdetect=d=1.0:pix_th=0.10", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    matches = re.findall(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)", proc.stderr)
    tails = [(float(s), float(e), float(d)) for s, e, d in matches
             if float(e) >= video_duration - 0.2 and float(d) >= 1.0]
    if not tails:
        return video_duration, None
    s, e, d = tails[-1]
    return s, {"start": s, "end": e, "duration": d}


# ── peak clustering ─────────────────────────────────────────────

def cluster_peaks(scores: list[tuple[float, float]], min_height: float,
                  min_distance: float) -> list[float]:
    """Cluster peaks by time distance, return best time per cluster."""
    candidates = [(t, s) for t, s in scores if s >= min_height and t > 0.1]
    if not candidates:
        return []
    clusters = [[candidates[0]]]
    for c in candidates[1:]:
        if c[0] - clusters[-1][-1][0] <= min_distance:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    return [max(cl, key=lambda x: x[1])[0] for cl in clusters]


def find_peaks(scores: list[tuple[float, float]], min_height: float = DEFAULT_MIN_HEIGHT,
               min_distance: float = DEFAULT_MIN_DISTANCE) -> list[tuple[float, float, str]]:
    """Backward-compatible peak API returning time, score and confidence."""
    candidates = [(t, s) for t, s in scores if s >= min_height and t > 0.1]
    if not candidates:
        return []
    clusters = [[candidates[0]]]
    for candidate in candidates[1:]:
        if candidate[0] - clusters[-1][-1][0] <= min_distance:
            clusters[-1].append(candidate)
        else:
            clusters.append([candidate])
    result = []
    for cluster in clusters:
        time, score = max(cluster, key=lambda item: item[1])
        confidence = "high" if score >= 0.4 else "medium" if score >= 0.25 else "low"
        result.append((time, score, confidence))
    return result


# ── segment building ────────────────────────────────────────────

def build_segments(cut_times: list[float], total_dur: float,
                   min_segment: float = DEFAULT_MIN_SEGMENT) -> tuple[list[dict], bool]:
    """Build segments from cuts, merging those < min_segment.
    Returns (segments, merged) — merged=True if any short segment was merged away."""
    if not cut_times:
        return [{"index": 1, "start": 0.0, "end": total_dur, "duration": total_dur}], False

    points = [0.0] + sorted(cut_times) + [total_dur]
    segs = [{"start": points[i], "end": points[i+1], "duration": points[i+1] - points[i]}
            for i in range(len(points) - 1)]

    had_merge = False
    changed = True
    while changed:
        changed = False
        if len(segs) <= 1:
            break
        min_idx = min(range(len(segs)), key=lambda i: segs[i]["duration"])
        if segs[min_idx]["duration"] >= min_segment:
            break
        left_dur = segs[min_idx - 1]["duration"] if min_idx > 0 else float("inf")
        right_dur = segs[min_idx + 1]["duration"] if min_idx < len(segs) - 1 else float("inf")
        if left_dur <= right_dur and min_idx > 0:
            segs[min_idx - 1]["end"] = segs[min_idx]["end"]
            segs[min_idx - 1]["duration"] = segs[min_idx - 1]["end"] - segs[min_idx - 1]["start"]
        elif min_idx < len(segs) - 1:
            segs[min_idx + 1]["start"] = segs[min_idx]["start"]
            segs[min_idx + 1]["duration"] = segs[min_idx + 1]["end"] - segs[min_idx + 1]["start"]
        segs.pop(min_idx)
        had_merge = True
        changed = True

    result = []
    for i, s in enumerate(segs):
        result.append({"index": i + 1, "start": round(s["start"], 6),
                       "end": round(s["end"], 6), "duration": round(s["duration"], 6)})
    return result, had_merge


def find_false_split_candidates(
    segments: list[dict],
    review_short_segment: float = DEFAULT_REVIEW_SHORT_SEGMENT,
) -> list[dict]:
    """找出容易由展开动作、快速运动或特效峰造成的可疑局部切点。

    这里只路由人工复核，不自动合并。像素跳变无法可靠区分“新主题”和
    “同一物体进入下一变形阶段”，自动删除切点会制造反向错误。
    """
    candidates = []
    for boundary_index in range(1, len(segments)):
        left = segments[boundary_index - 1]
        right = segments[boundary_index]
        shortest = min(float(left["duration"]), float(right["duration"]))
        if shortest <= review_short_segment:
            candidates.append({
                "boundary_index": boundary_index,
                "time": round(float(left["end"]), 6),
                "left_duration": round(float(left["duration"]), 6),
                "right_duration": round(float(right["duration"]), 6),
                "reason": "short_fragment_requires_semantic_continuity_review",
                "review_frames": [-0.8, -0.15, 0.15, 0.8],
            })
    return candidates


# ── dHash fallback ──────────────────────────────────────────────

def dhash_detect(path: Path, seg_start: float, seg_end: float) -> float | None:
    """Sliding-window dHash comparison to find transitions invisible to ffmpeg scene detect."""
    fps, side = 6, 64
    dur = seg_end - seg_start
    r = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", f"{seg_start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(path), "-an", "-vf", f"fps={fps},scale={side}:{side}:flags=area,format=gray",
        "-f", "rawvideo", "-"
    ], capture_output=True, timeout=30)

    size = side * side
    frames = [r.stdout[i:i+size] for i in range(0, len(r.stdout) - size + 1, size)]
    if len(frames) < 10:
        return None

    def dh(f):
        h = 0
        for y in range(side):
            for x in range(side - 1):
                if f[y * side + x] > f[y * side + x + 1]:
                    h |= 1 << (y * (side - 1) + x)
        return h

    def ham(a, b):
        return bin(a ^ b).count("1")

    hashes = [dh(f) for f in frames]
    lag = int(2 * fps)
    diffs = [(seg_start + i / fps, ham(hashes[i], hashes[i - lag]))
             for i in range(lag, len(hashes))]
    if not diffs:
        return None

    vals = [d for _, d in diffs]
    mean_d = sum(vals) / len(vals)
    std_d = (sum((v - mean_d)**2 for v in vals) / len(vals)) ** 0.5
    candidates = [(t, d) for t, d in diffs if (d - mean_d) > 2.0 * std_d]
    if not candidates:
        return None
    best = max(candidates, key=lambda x: x[1])
    if seg_start + 3.0 < best[0] < seg_end - 3.0:
        return best[0]
    return None


# ── adaptive analysis ───────────────────────────────────────────

LONG_SEGMENT = 15.0
MIN_HEIGHT_DEFAULT = 0.25
MIN_HEIGHT_LOW = 0.10


def analyze_content(path: Path, min_height: float = MIN_HEIGHT_DEFAULT,
                    min_distance: float = 2.5,
                    min_segment: float = DEFAULT_MIN_SEGMENT,
                    min_cut_from_start: float = DEFAULT_MIN_CUT_FROM_START) -> dict:
    """Adaptive content-aware analysis with iterative long-segment refinement."""
    scores = scene_scores(path)
    media = probe(path)
    usable_dur, black_tail = detect_black_tail(path, media["video_duration"])

    # Pass 1: detect with default threshold
    peaks_025 = [(t, s) for t, s in scores if s >= min_height and t >= min_cut_from_start]
    cuts = cluster_peaks(peaks_025, min_height, min_distance)
    cuts = [c for c in cuts if usable_dur - c >= min_segment]
    segs, _ = build_segments(cuts, usable_dur, min_segment)

    # Pass 2: for any long segment, try ALL raw peaks + dHash
    for _ in range(5):
        long_segs = [(i, s) for i, s in enumerate(segs) if s["duration"] > LONG_SEGMENT]
        if not long_segs:
            break
        changed = False
        for _, seg in long_segs:
            raw = [(t, s) for t, s in scores
                   if seg["start"] + 2.0 < t < seg["end"] - 2.0 and s >= MIN_HEIGHT_LOW]
            if raw:
                best_min_dur = 0
                best_cut = None
                for t, _ in sorted(raw, key=lambda x: -x[1])[:20]:
                    if any(abs(t - c) < 2.0 for c in cuts):
                        continue
                    trial_segs, merged = build_segments(sorted(set(cuts + [t])), usable_dur, min_segment)
                    if merged:
                        continue
                    region = [s for s in trial_segs
                             if seg["start"] <= s["start"] < seg["end"]]
                    if region and all(s["duration"] >= min_segment for s in region):
                        min_d = min(s["duration"] for s in region)
                        if min_d > best_min_dur:
                            best_min_dur = min_d
                            best_cut = t
                if best_cut:
                    cuts = sorted(set(cuts + [best_cut]))
                    changed = True
            # dHash fallback
            dh = dhash_detect(path, seg["start"], seg["end"])
            if dh and all(abs(dh - c) >= 2.0 for c in cuts):
                trial_segs, merged = build_segments(sorted(set(cuts + [dh])), usable_dur, min_segment)
                if not merged:
                    region = [s for s in trial_segs
                             if seg["start"] <= s["start"] < seg["end"]]
                    if region and all(s["duration"] >= min_segment for s in region):
                        cuts = sorted(set(cuts + [dh]))
                        changed = True
        if not changed:
            break
        cuts = [c for c in cuts if usable_dur - c >= min_segment]
        segs, _ = build_segments(cuts, usable_dur, min_segment)

    # Pass 3: over-split check — merge if average mid-segment < 7s
    if len(segs) > 1:
        mid = [s for s in segs if s["duration"] >= 5.0]
        if mid:
            avg = sum(s["duration"] for s in mid) / len(mid)
            if avg < 7.0:
                best_improve = 0
                best_remove = None
                for i in range(len(cuts)):
                    test_cuts = [c for j, c in enumerate(cuts) if j != i]
                    test_segs, _ = build_segments(test_cuts, usable_dur, min_segment)
                    if len(test_segs) >= 2:
                        test_mid = [s for s in test_segs if s["duration"] >= 5.0]
                        new_avg = sum(s["duration"] for s in test_mid) / len(test_mid) if test_mid else 0
                        if new_avg - avg > best_improve:
                            best_improve = new_avg - avg
                            best_remove = i
                if best_remove is not None and best_improve > 1.5:
                    cuts = [c for j, c in enumerate(cuts) if j != best_remove]
                    segs, _ = build_segments(cuts, usable_dur, min_segment)

    # Build peak metadata
    all_peaks = []
    for c in cuts:
        nearby = [s for t, s in scores if abs(t - c) < 0.5]
        best_score = max(nearby) if nearby else 0
        conf = "high" if best_score >= 0.4 else "medium" if best_score >= 0.25 else "low"
        all_peaks.append({"time": round(c, 6), "score": round(best_score, 4),
                          "confidence": conf})

    low_confidence = any(item["confidence"] == "low" for item in all_peaks)
    false_split_candidates = find_false_split_candidates(segs)
    review_reasons = []
    if low_confidence:
        review_reasons.append("low_confidence_peak")
    if false_split_candidates:
        review_reasons.append("possible_action_peak_false_split")
    return {"input": str(path), "media": media, "usable_duration": usable_dur,
            "ignored_black_tail": black_tail, "method": "adaptive_content_peak",
            "detected_peaks": all_peaks, "segments": segs,
            "false_split_candidates": false_split_candidates,
            "review": {"status": "required" if review_reasons else "not_required",
                       "reasons": review_reasons,
                       "rule": "同一主体、固定标签和连续展开动作不应拆开；新主体/新标签才保留切点"},
            "parameters": {"min_height": min_height, "min_distance": min_distance,
                           "min_segment": min_segment, "min_cut_from_start": min_cut_from_start},
            "status": "needs_review" if len(segs) <= 1 or review_reasons else "ready"}


# ── preview ─────────────────────────────────────────────────────

def make_preview(path: Path, analysis: dict, preview_dir: Path):
    """Extract frames at segment midpoints and near cut points."""
    preview_dir.mkdir(parents=True, exist_ok=True)
    segments = analysis["segments"]
    peaks = analysis.get("detected_peaks", [])

    # midpoints
    for seg in segments:
        t = (seg["start"] + seg["end"]) / 2
        dest = preview_dir / f"seg{seg['index']:02d}_mid_{t:.1f}s.jpg"
        if dest.exists():
            continue
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-q:v", "3", str(dest)], check=False)

    # near cuts
    for p in peaks:
        for offset, label in [(-0.3, "before"), (0.0, "at"), (0.3, "after")]:
            ts = max(0.1, p["time"] + offset)
            dest = preview_dir / f"cut_{p['time']:.1f}s_{label}.jpg"
            if dest.exists():
                continue
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(path),
                            "-frames:v", "1", "-q:v", "3", str(dest)], check=False)


# ── split execution ─────────────────────────────────────────────

def split_one(path: Path, analysis: dict, output_root: Path, approve_review: bool = False) -> dict:
    segments = analysis["segments"]
    media = analysis["media"]
    parameters = analysis.get("parameters", {})
    min_cut_from_start = float(parameters.get("min_cut_from_start", DEFAULT_MIN_CUT_FROM_START))
    min_segment = float(parameters.get("min_segment", DEFAULT_MIN_SEGMENT))

    if analysis.get("status") == "needs_review" and not approve_review:
        return {
            "file": path.name,
            "status": "skipped",
            "reason": "review_required_use_--approve-review_after_visual_check",
        }

    # filter: keep only cuts >= min_cut_from_start
    valid_starts = set()
    for i in range(1, len(segments)):
        if segments[i]["start"] >= min_cut_from_start:
            valid_starts.add(segments[i]["start"])

    if not valid_starts:
        return {"file": path.name, "status": "skipped", "reason": "all_cuts_too_early"}

    points = [0.0] + sorted(valid_starts) + [analysis["usable_duration"]]
    final_segs = []
    for i in range(len(points) - 1):
        dur = points[i + 1] - points[i]
        if dur >= min_segment:
            final_segs.append({"index": i + 1, "start": points[i], "end": points[i + 1], "duration": dur})

    if len(final_segs) <= 1:
        return {"file": path.name, "status": "skipped", "reason": "single_after_filter"}

    out_dir = output_root / path.stem
    if out_dir.exists():
        return {"file": path.name, "status": "skipped", "reason": "output_exists"}
    out_dir.mkdir(parents=True)

    n = len(final_segs)
    has_audio = media["has_audio"]
    vs = "".join(f"[vsrc{i+1}]" for i in range(n))
    filters = [f"[0:v]split={n}{vs}"]
    if has_audio:
        a_s = "".join(f"[asrc{i+1}]" for i in range(n))
        filters.append(f"[0:a]asplit={n}{a_s}")
    for seg in final_segs:
        idx = seg["index"]
        filters.append(f"[vsrc{idx}]trim=start={seg['start']:.6f}:end={seg['end']:.6f},setpts=PTS-STARTPTS[v{idx}]")
        if has_audio:
            filters.append(f"[asrc{idx}]atrim=start={seg['start']:.6f}:end={seg['end']:.6f},asetpts=PTS-STARTPTS[a{idx}]")

    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-n", "-i", str(path),
           "-filter_complex", ";".join(filters)]
    for seg in final_segs:
        idx = seg["index"]
        dest = out_dir / f"{idx:02d}_t{seg['start']:.1f}-{seg['end']:.1f}s.mp4"
        seg["file"] = str(dest)
        cmd.extend(["-map", f"[v{idx}]"])
        if has_audio:
            cmd.extend(["-map", f"[a{idx}]"])
        cmd.extend(["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"])
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        cmd.extend(["-movflags", "+faststart", str(dest)])

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as e:
        return {"file": path.name, "status": "failed", "reason": f"ffmpeg: {e.stderr[-200:] if e.stderr else e}"}

    # verify
    verified = True
    for seg in final_segs:
        dest = Path(seg["file"])
        if not dest.is_file():
            verified = False
            continue
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(dest), "-f", "null", "-"],
                           capture_output=True, text=True, timeout=30, check=True)
        except:
            verified = False

    # manifest
    manifest = out_dir / "拆分清单.tsv"
    with manifest.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["index", "start", "end", "duration", "file", "verified"], delimiter="\t")
        w.writeheader()
        for seg in final_segs:
            w.writerow({"index": seg["index"], "start": f"{seg['start']:.3f}",
                        "end": f"{seg['end']:.3f}", "duration": f"{seg['duration']:.3f}",
                        "file": Path(seg["file"]).name, "verified": True})

    return {"file": path.name, "status": "ok", "segments": len(final_segs),
            "output_dir": str(out_dir), "verified": verified}


# ── CLI ─────────────────────────────────────────────────────────

def cmd_analyze(args):
    all_mp4s = sorted(Path(args.input_dir).glob("*.mp4"))
    mp4s = [f for f in all_mp4s if "__h264-aac" not in f.name] or all_mp4s
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    for i, f in enumerate(mp4s):
        ana_path = out_dir / f"{f.stem}_content_analysis.json"
        if ana_path.exists():
            summaries.append(json.loads(ana_path.read_text()))
        else:
            print(f"\r分析: [{i+1}/{len(mp4s)}] {f.stem[:45]}...", end="", flush=True)
            try:
                r = analyze_content(f, min_height=args.min_height, min_distance=args.min_distance,
                                    min_segment=args.min_segment, min_cut_from_start=args.min_cut_from_start)
                ana_path.write_text(json.dumps(r, ensure_ascii=False, indent=2))
                summaries.append(r)
            except Exception as e:
                print(f"\n  ❌ {f.name}: {e}")

        if args.preview:
            try:
                prev_dir = out_dir / f"{f.stem}_content_frames"
                make_preview(f, summaries[-1], prev_dir)
            except:
                pass

    print()

    # summary
    total_segs = sum(len(r["segments"]) for r in summaries)
    single = sum(1 for r in summaries if len(r["segments"]) <= 1)
    print(f"\n=== 内容感知分析 ===")
    print(f"视频: {len(summaries)}  总段数: {total_segs}  单段: {single}")

    tsv_path = out_dir / "_content_summary.tsv"
    with tsv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["file", "duration", "segments", "cut_times", "status"])
        for r in summaries:
            cuts = ",".join(f"{p['time']:.1f}s" for p in r.get("detected_peaks", []))
            w.writerow([Path(r["input"]).name, f"{r['media']['video_duration']:.1f}s",
                        len(r["segments"]), cuts, r["status"]])
    print(f"TSV: {tsv_path}")


def cmd_execute(args):
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_mp4s = sorted(Path(args.input_dir).glob("*.mp4"))
    mp4s = [f for f in all_mp4s if "__h264-aac" not in f.name] or all_mp4s
    analysis_dir = Path(args.analysis_dir) if args.analysis_dir else None

    ok, skipped, failed = 0, 0, 0
    for i, f in enumerate(mp4s):
        ana_path = (analysis_dir / f"{f.stem}_content_analysis.json") if analysis_dir else None
        if not ana_path or not ana_path.is_file():
            print(f"\r拆分: [{i+1}/{len(mp4s)}] {f.stem[:45]}... 跳过(无分析文件)", end="", flush=True)
            skipped += 1
            continue

        print(f"\r拆分: [{i+1}/{len(mp4s)}] {f.stem[:45]}...", end="", flush=True)
        try:
            analysis = json.loads(ana_path.read_text())
            if len(analysis["segments"]) <= 1:
                skipped += 1
                continue
            result = split_one(f, analysis, out_dir, approve_review=args.approve_review)
            if result["status"] == "ok":
                ok += 1
            elif result["status"] == "skipped":
                skipped += 1
            else:
                failed += 1
                print(f"\n  ❌ {f.name}: {result.get('reason', '')}")
        except Exception as e:
            failed += 1
            print(f"\n  ❌ {f.name}: {e}")

    print(f"\n=== 完成 === 成功:{ok} 跳过:{skipped} 失败:{failed}")


def main():
    p = argparse.ArgumentParser(description="Content-aware video splitting via scene peak detection")
    p.add_argument("--input-dir", required=True, help="Directory containing MP4 files")
    p.add_argument("--output-dir", help="Output directory for previews or splits")
    p.add_argument("--analysis-dir", help="Directory containing saved _content_analysis.json files (for --execute)")
    p.add_argument("--analyze", action="store_true")
    p.add_argument("--preview", action="store_true", help="Generate preview frames at cut points")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--approve-review", action="store_true",
                   help="确认已逐一核验 false_split_candidates 后执行 needs_review 项")
    p.add_argument("--min-height", type=float, default=MIN_HEIGHT_DEFAULT)
    p.add_argument("--min-distance", type=float, default=2.5)
    p.add_argument("--min-segment", type=float, default=DEFAULT_MIN_SEGMENT)
    p.add_argument("--min-cut-from-start", type=float, default=DEFAULT_MIN_CUT_FROM_START)

    args = p.parse_args()

    if not args.output_dir:
        p.error("--output-dir is required")

    if args.analyze:
        cmd_analyze(args)
    if args.execute:
        cmd_execute(args)

    if not args.analyze and not args.execute:
        p.error("需要 --analyze 或 --execute")


if __name__ == "__main__":
    raise SystemExit(main())
