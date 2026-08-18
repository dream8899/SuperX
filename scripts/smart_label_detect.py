#!/usr/bin/env python3
"""Intelligent label/logo detection — temporal consensus + creator memory + adaptive threshold.

Three-phase approach:
  1. Temporal consensus: sample frames across the full video. Static overlay elements
     (labels, logos) persist in all frames; content moves. Intersection = real overlays.
  2. Creator memory: save successful crop params per creator. On re-run, use as prior
     and only verify. Self-improves with each confirmed crop.
  3. Adaptive threshold: percentile-based per-video, not fixed multipliers.

Usage:
  # Analyze one video
  python3 smart_label_detect.py VIDEO.mp4 [--creator NAME] [--memory MEMORY_DIR]

  # Analyze a directory, save creator memory
  python3 smart_label_detect.py --input-dir DIR --memory MEMORY_DIR

  # Use saved memory to process (fast path)
  python3 smart_label_detect.py VIDEO.mp4 --creator NAME --memory MEMORY_DIR
"""

import argparse, csv, difflib, json, re, shutil, statistics, subprocess, time
from pathlib import Path

MEMORY_VERSION = 2
ANALYSIS_WIDTH = 256
SAMPLE_COUNT = 8
TARGET_OCR_SAMPLES = 4
GRID_SIZE = 8


def check_playability(path: Path) -> dict:
    """Decode the complete file so partial/corrupt MP4s are not treated as usable."""
    try:
        process = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": "full_decode_timeout"}
    if process.returncode:
        return {"status": "failed", "error": process.stderr.strip()[-1000:] or "full_decode_failed"}
    return {"status": "verified"}


# ── Core detection ──────────────────────────────────────────────

def extract_frames(path: Path, count: int = SAMPLE_COUNT) -> tuple[list[bytes], int, int, float, int, int]:
    """Extract evenly-spaced, downscaled grayscale frames for efficient analysis."""
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=10
    ).stdout.strip())

    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=10
    )
    original_w, original_h = map(int, r.stdout.strip().split(","))
    analysis_w = min(ANALYSIS_WIDTH, original_w)
    analysis_h = max(2, round((original_h * analysis_w / original_w) / 2) * 2)

    interval = max(0.5, (dur - 1.0) / count)
    frames = []
    for i in range(count):
        t = 0.5 + i * interval
        if t >= dur - 0.3:
            break
        r = subprocess.run([
            "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
            "-an", "-frames:v", "1",
            "-vf", f"scale={analysis_w}:{analysis_h}:flags=area,format=gray",
            "-f", "rawvideo", "-"
        ], capture_output=True, timeout=10)
        if len(r.stdout) == analysis_w * analysis_h:
            frames.append(r.stdout)

    if len(frames) < 2:
        raise RuntimeError("有效采样帧不足，无法进行时序共识检测")
    return frames, analysis_w, analysis_h, dur, original_w, original_h


def row_contrast(frame: bytes, w: int, h: int) -> list[float]:
    """Per-row standard deviation (contrast)."""
    result = [0.0] * h
    for y in range(h):
        row = frame[y * w:(y + 1) * w]
        mean = statistics.fmean(row)
        result[y] = statistics.fmean((v - mean) ** 2 for v in row) ** 0.5
    return result


def temporal_consensus(frames: list[bytes], w: int, h: int) -> tuple[list[float], list[float]]:
    """
    Compute per-row contrast for each frame, then find the INTERSECTION
    of high-contrast regions across frames. Static overlays appear in all frames.
    Returns (mean_contrast, stability_score).
    """
    n = len(frames)
    if n < 2:
        c = row_contrast(frames[0], w, h)
        return c, [1.0] * h

    all_contrasts = [row_contrast(f, w, h) for f in frames]

    # mean contrast across frames
    mean_c = [0.0] * h
    for y in range(h):
        mean_c[y] = sum(all_contrasts[i][y] for i in range(n)) / n

    # stability: how consistent the contrast is across frames
    # High stability + high contrast = static overlay (label/logo)
    # High contrast + low stability = moving content (ignore)
    stability = [0.0] * h
    for y in range(h):
        vals = [all_contrasts[i][y] for i in range(n)]
        m = mean_c[y]
        if m > 0:
            # coefficient of variation → lower = more stable
            std = (sum((v - m) ** 2 for v in vals) / n) ** 0.5
            stability[y] = 1.0 - min(1.0, std / m)  # 1.0 = perfectly stable
        else:
            stability[y] = 0.0

    return mean_c, stability


def adaptive_threshold(values: list[float], percentile: float = 85) -> float:
    """Adaptive threshold based on percentile of the distribution."""
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * percentile / 100)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def _tile_metrics(frame: bytes, w: int, x1: int, y1: int, x2: int, y2: int) -> tuple[float, float]:
    values = []
    for y in range(y1, y2):
        values.extend(frame[y * w + x1:y * w + x2])
    mean = sum(values) / len(values)
    variance = max(0.0, sum(value * value for value in values) / len(values) - mean * mean)
    return mean, variance ** 0.5


def _ocr_pgm(pgm: bytes) -> str:
    if not shutil.which("tesseract"):
        return ""
    process = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng"],
        input=pgm, capture_output=True, timeout=15,
    )
    return process.stdout.decode(errors="ignore").strip() if process.returncode == 0 else ""


def _ocr_image(image: bytes) -> str:
    if not shutil.which("tesseract"):
        return ""
    process = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng"],
        input=image, capture_output=True, timeout=20,
    )
    return process.stdout.decode(errors="ignore").strip() if process.returncode == 0 else ""


def _target_text_score(text: str, target: str) -> float:
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    actual, expected = normalize(text), normalize(target)
    if not actual or not expected:
        return 0.0
    return difflib.SequenceMatcher(None, actual, expected).ratio()


def match_target_label(frames: list[bytes], w: int, h: int, box: list[int], target: str) -> dict:
    """OCR a candidate label across samples; keep it only when the target is stable."""
    x1, y1, x2, y2 = [max(0, int(value)) for value in box]
    x2, y2 = min(w, x2), min(h, y2)
    texts, scores = [], []
    for frame in frames:
        crop = bytearray()
        for y in range(y1, y2):
            crop.extend(frame[y * w + x1:x2])
        # OCR needs more than the 256px analysis raster; nearest-neighbor enlargement
        # gives Tesseract enough glyph pixels without introducing an image dependency.
        scale = 4
        enlarged = bytearray()
        row_width = x2 - x1
        for offset in range(0, len(crop), row_width):
            row = crop[offset:offset + row_width]
            enlarged.extend(value for pixel in row for value in (pixel,) * scale for _ in range(scale))
        pgm = f"P5\n{row_width * scale} {(y2-y1) * scale}\n255\n".encode() + bytes(enlarged)
        text = _ocr_pgm(pgm)
        texts.append(text)
        scores.append(_target_text_score(text, target))
    return {"target_label": target, "ocr_texts": texts, "target_match_score": round(max(scores, default=0.0), 3),
            "target_match_rate": round(sum(score >= 0.55 for score in scores) / max(1, len(scores)), 3)}


def match_target_label_media(path: Path, duration: float, box: list[int], source_w: int,
                             source_h: int, analysis_w: int, analysis_h: int, target: str) -> dict:
    """OCR the original-resolution candidate; low-res analysis pixels are insufficient for glyph matching."""
    sx, sy = source_w / analysis_w, source_h / analysis_h
    x1, y1, x2, y2 = [round(value) for value in (box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy)]
    texts, scores = [], []
    for fraction in (0.05, 0.35, 0.65, 0.95):
        timestamp = max(0.1, min(duration - 0.3, duration * fraction))
        process = subprocess.run([
            "ffmpeg", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(path), "-an",
            "-frames:v", "1", "-vf", f"crop={x2-x1}:{y2-y1}:{x1}:{y1},scale=960:-2:flags=neighbor,format=gray",
            "-f", "image2pipe", "-vcodec", "png", "-"
        ], capture_output=True, timeout=20)
        text = _ocr_image(process.stdout) if process.returncode == 0 else ""
        texts.append(text)
        scores.append(_target_text_score(text, target))
    return {"target_label": target, "ocr_texts": texts, "target_match_score": round(max(scores, default=0.0), 3),
            "target_match_rate": round(sum(score >= 0.55 for score in scores) / max(1, len(scores)), 3)}


def detect_spatial_candidates(frames: list[bytes], w: int, h: int) -> list[dict]:
    """Locate persistent high-detail overlay candidates on a coarse 2D grid."""
    cols = (w + GRID_SIZE - 1) // GRID_SIZE
    rows = (h + GRID_SIZE - 1) // GRID_SIZE
    metrics = {}
    contrasts = []
    for gy in range(rows):
        for gx in range(cols):
            x1, y1 = gx * GRID_SIZE, gy * GRID_SIZE
            x2, y2 = min(w, x1 + GRID_SIZE), min(h, y1 + GRID_SIZE)
            tile = [_tile_metrics(frame, w, x1, y1, x2, y2) for frame in frames]
            means = [item[0] for item in tile]
            detail = [item[1] for item in tile]
            mean_detail = statistics.fmean(detail)
            detail_cv = statistics.pstdev(detail) / max(mean_detail, 1.0)
            intensity_cv = statistics.pstdev(means) / 32.0
            persistence = 1.0 - min(1.0, detail_cv * 0.7 + intensity_cv * 0.3)
            metrics[(gx, gy)] = (mean_detail, persistence)
            contrasts.append(mean_detail)

    high_threshold = adaptive_threshold(contrasts, 84)
    active = set()
    for (gx, gy), (contrast, persistence) in metrics.items():
        edge = gx < cols * 0.25 or gx >= cols * 0.75 or gy < rows * 0.25 or gy >= rows * 0.75
        if edge and contrast >= high_threshold and persistence >= 0.55:
            active.add((gx, gy))

    components = []
    while active:
        seed = active.pop()
        component = {seed}
        queue = [seed]
        while queue:
            gx, gy = queue.pop()
            for nx in range(gx - 1, gx + 2):
                for ny in range(gy - 1, gy + 2):
                    if (nx, ny) in active:
                        active.remove((nx, ny))
                        component.add((nx, ny))
                        queue.append((nx, ny))
        if len(component) >= 2:
            components.append(component)

    candidates = []
    for component in components:
        xs = [item[0] for item in component]
        ys = [item[1] for item in component]
        x1, y1 = min(xs) * GRID_SIZE, min(ys) * GRID_SIZE
        x2, y2 = min(w, (max(xs) + 1) * GRID_SIZE), min(h, (max(ys) + 1) * GRID_SIZE)
        width, height = x2 - x1, y2 - y1
        touches = []
        margin_x, margin_y = max(GRID_SIZE, int(w * 0.04)), max(GRID_SIZE, int(h * 0.04))
        if x1 <= margin_x:
            touches.append("left")
        if x2 >= w - margin_x:
            touches.append("right")
        if y1 <= margin_y:
            touches.append("top")
        if y2 >= h - margin_y:
            touches.append("bottom")
        values = [metrics[item] for item in component]
        persistence = statistics.fmean(item[1] for item in values)
        contrast = statistics.fmean(item[0] for item in values)
        kind = "text_label" if width / max(height, 1) >= 2.2 or width >= w * 0.28 else "logo"
        confidence = min(0.99, 0.45 * persistence + 0.35 * min(1.0, contrast / max(high_threshold, 1.0)) + 0.20 * bool(touches))
        candidates.append({
            "kind": kind,
            "box": [x1, y1, x2, y2],
            "touches_edges": touches,
            "confidence": round(confidence, 3),
            "persistence": round(persistence, 3),
        })
    return sorted(candidates, key=lambda item: (-item["confidence"], item["box"][1], item["box"][0]))


def scale_candidates(candidates: list[dict], source_w: int, source_h: int,
                     analysis_w: int, analysis_h: int) -> list[dict]:
    scaled = []
    sx, sy = source_w / analysis_w, source_h / analysis_h
    for item in candidates:
        x1, y1, x2, y2 = item["box"]
        scaled.append({**item, "box": [round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy)]})
    return scaled


def detect_overlays(frames: list[bytes], w: int, h: int, target_label: str = "") -> dict:
    """
    Primary detection using temporal consensus.
    Returns detected overlay regions.
    """
    mean_c, stability = temporal_consensus(frames, w, h)
    spatial_candidates = detect_spatial_candidates(frames, w, h)

    # baseline from middle 40-60% of frame (assumed content area)
    mid_start, mid_end = int(h * 0.4), int(h * 0.6)
    baseline_c = statistics.mean(mean_c[mid_start:mid_end])

    # ── detect text labels (high contrast + stable) ──
    # text = contrast > 95th percentile AND stability > 0.7
    contrast_threshold_text = adaptive_threshold(mean_c, 92)
    text_rows = set()
    for y in range(h):
        if mean_c[y] > contrast_threshold_text and stability[y] > 0.6:
            text_rows.add(y)

    # ── detect logo region (brightness gradient + contrast) ──
    # Logo = brightness drops < 0.82× mid AND contrast > 0.85× baseline.
    # Scan upward from bottom, requiring sustained signal (not noise).
    logo_rows = set()
    row_brightness = [0.0] * h
    for f in frames:
        for y in range(h):
            row = f[y * w:(y + 1) * w]
            row_brightness[y] += statistics.fmean(row) / len(frames)

    mid_brightness = statistics.mean(row_brightness[int(h*0.35):int(h*0.55)])

    # Find logo: scan upward, count consecutive rows matching logo criteria
    logo_start = h
    consecutive = 0
    MIN_CONSECUTIVE = 12  # at least 12 rows (~0.6% of 1080p)
    for y in range(h - 3, int(h * 0.65), -1):
        b_ratio = row_brightness[y] / mid_brightness if mid_brightness > 0 else 1
        c_ratio = mean_c[y] / baseline_c if baseline_c > 0 else 0
        is_logo = b_ratio < 0.82 and c_ratio > 0.85
        if is_logo:
            consecutive += 1
            if consecutive >= MIN_CONSECUTIVE and y < logo_start:
                logo_start = y
        else:
            if consecutive >= MIN_CONSECUTIVE:
                break  # exited sustained logo region
            consecutive = 0

    if logo_start < h:
        for y in range(logo_start, h):
            if y not in text_rows:
                logo_rows.add(y)

    # ── find contiguous regions ──
    def find_bands(rows: set, min_width: int = 5) -> list[tuple[int, int]]:
        """Find contiguous bands of rows."""
        if not rows:
            return []
        sorted_rows = sorted(rows)
        bands = []
        start = sorted_rows[0]
        prev = start
        for y in sorted_rows[1:]:
            if y - prev <= 2:  # gap <= 2px → same band
                prev = y
            else:
                if prev - start >= min_width:
                    bands.append((start, prev))
                start = y
                prev = y
        if prev - start >= min_width:
            bands.append((start, prev))
        return bands

    text_bands = find_bands(text_rows, min_width=8)
    logo_bands = find_bands(logo_rows, min_width=10)

    # ── classify by position ──
    top_text, bot_text, logo = None, None, None

    for y1, y2 in text_bands:
        center = (y1 + y2) / 2
        if center < h * 0.3:
            if top_text is None or (y2 - y1) > (top_text[1] - top_text[0]):
                top_text = (y1, y2)
        elif center > h * 0.7:
            if bot_text is None or (y2 - y1) > (bot_text[1] - bot_text[0]):
                bot_text = (y1, y2)

    for y1, y2 in logo_bands:
        center = (y1 + y2) / 2
        if center > h * 0.7:  # logo always at bottom
            if logo is None or (y2 - y1) > (logo[1] - logo[0]):
                logo = (y1, y2)

    text_pos = "none"
    if top_text and bot_text:
        text_pos = "both"
    elif top_text:
        text_pos = "top"
    elif bot_text:
        text_pos = "bottom"

    return {
        "text_position": text_pos,
        "top_text_band": list(top_text) if top_text else None,
        "bot_text_band": list(bot_text) if bot_text else None,
        "logo_band": list(logo) if logo else None,
        "overlay_candidates": spatial_candidates,
        "baseline_contrast": round(baseline_c, 1),
        "text_threshold": round(contrast_threshold_text, 1),
        "logo_threshold": round(baseline_c * 0.88, 1),
        "frame_count": len(frames),
        "resolution": f"{w}x{h}",
        "duration": 0,  # filled later
    }


# ── Creator Memory ──────────────────────────────────────────────

def load_memory(memory_dir: Path, creator: str) -> dict | None:
    """Load saved crop parameters for a creator."""
    mem_file = memory_dir / f"{creator}.json"
    if not mem_file.is_file():
        return None
    data = json.loads(mem_file.read_text())
    if data.get("version") != MEMORY_VERSION:
        return None
    return data


def save_memory(memory_dir: Path, creator: str, crop_params: dict, detection: dict):
    """Save successful crop parameters for future reuse."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    mem_file = memory_dir / f"{creator}.json"

    # merge with existing memory
    existing = {}
    if mem_file.is_file():
        existing = json.loads(mem_file.read_text())

    # evolve: weighted average with previous crops
    history = existing.get("crop_history", [])
    history.append({
        "timestamp": time.time(),
        "top_crop": crop_params.get("top_crop_px", 0),
        "bot_crop": crop_params.get("bot_crop_px", 0),
        "left_crop": crop_params.get("left_crop_px", 0),
        "right_crop": crop_params.get("right_crop_px", 0),
        "text_position": detection.get("text_position", "none"),
        "resolution": detection.get("resolution", ""),
    })
    # keep last 10
    history = history[-10:]

    # compute consensus crop from history (median)
    top_crops = [h["top_crop"] for h in history]
    bot_crops = [h["bot_crop"] for h in history]
    left_crops = [h.get("left_crop", 0) for h in history]
    right_crops = [h.get("right_crop", 0) for h in history]
    text_positions = [h["text_position"] for h in history]
    resolutions = [h["resolution"] for h in history if h.get("resolution")]

    data = {
        "version": MEMORY_VERSION,
        "creator": creator,
        "updated": time.time(),
        "consensus_top_crop": int(statistics.median(top_crops)) if top_crops else 0,
        "consensus_bot_crop": int(statistics.median(bot_crops)) if bot_crops else 0,
        "consensus_left_crop": int(statistics.median(left_crops)) if left_crops else 0,
        "consensus_right_crop": int(statistics.median(right_crops)) if right_crops else 0,
        "consensus_text_position": max(set(text_positions), key=text_positions.count) if text_positions else "none",
        "consensus_resolution": max(set(resolutions), key=resolutions.count) if resolutions else "",
        "crop_history": history,
    }
    mem_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def verify_memory(detection: dict, memory: dict, w: int, h: int,
                  fresh_crop: dict) -> dict | None:
    """
    Verify that saved memory still matches current video.
    Returns adjusted crop params if match, None if layout changed.
    """
    detected_pos = detection["text_position"]
    mem_pos = memory.get("consensus_text_position", "none")

    # text position must match
    if detected_pos != mem_pos:
        return None  # layout changed, need re-detection
    if memory.get("consensus_resolution") != detection.get("resolution"):
        return None

    top_crop = memory["consensus_top_crop"]
    bot_crop = memory["consensus_bot_crop"]
    left_crop = memory.get("consensus_left_crop", 0)
    right_crop = memory.get("consensus_right_crop", 0)

    # verify: is the saved crop region actually an overlay in this video?
    # (simple check: crop shouldn't be >30% of frame)
    if top_crop > h * 0.3 or bot_crop > h * 0.3 or left_crop > w * 0.3 or right_crop > w * 0.3:
        return None
    tolerance_y, tolerance_x = max(16, round(h * 0.04)), max(16, round(w * 0.04))
    comparisons = (
        (top_crop, fresh_crop["top_crop_px"], tolerance_y),
        (bot_crop, fresh_crop["bot_crop_px"], tolerance_y),
        (left_crop, fresh_crop["left_crop_px"], tolerance_x),
        (right_crop, fresh_crop["right_crop_px"], tolerance_x),
    )
    if any(abs(saved - detected) > tolerance for saved, detected, tolerance in comparisons):
        return None

    crop_width, crop_height = w - left_crop - right_crop, h - top_crop - bot_crop
    content_loss = 1.0 - (crop_width * crop_height) / (w * h)

    return {
        "top_crop_px": top_crop,
        "bot_crop_px": bot_crop,
        "left_crop_px": left_crop,
        "right_crop_px": right_crop,
        "text_position": mem_pos,
        "crop_rect": {"x": left_crop, "y": top_crop, "width": crop_width, "height": crop_height},
        "content_loss": round(content_loss, 4),
        "risk": "high" if content_loss > 0.20 else "medium" if content_loss > 0.10 else "low",
        "review_required": True,
        "source": "memory",
    }


# ── Crop calculation ────────────────────────────────────────────

def calculate_crop(detection: dict, w: int, h: int) -> dict:
    """Convert edge-touching overlays to a minimum-content-loss crop rectangle."""
    top_crop = 0
    bot_crop = 0
    left_crop = 0
    right_crop = 0

    top_text = detection.get("top_text_band")
    bot_text = detection.get("bot_text_band")
    logo = detection.get("logo_band")

    # top crop: just below top text label (with 5px margin)
    if top_text:
        top_crop = top_text[1] + 8

    # bottom crop: start of logo or bottom text, whichever is higher (with 5px margin)
    bot_start = h
    if logo:
        bot_start = min(bot_start, logo[0] - 5)
    if bot_text:
        bot_start = min(bot_start, bot_text[0] - 5)

    if bot_start < h:
        bot_crop = h - bot_start

    accepted_candidates = []
    held_candidates = []
    margin_x, margin_y = max(6, round(w * 0.006)), max(6, round(h * 0.006))
    for candidate in detection.get("overlay_candidates", []):
        x1, y1, x2, y2 = candidate["box"]
        edges = set(candidate.get("touches_edges", []))
        if candidate.get("confidence", 0) < 0.65:
            held_candidates.append({**candidate, "reason": "low_confidence"})
            continue
        options = [
            (min(h, y2 + margin_y) / h, "top", min(h, y2 + margin_y)),
            (min(h, h - y1 + margin_y) / h, "bottom", min(h, h - y1 + margin_y)),
            (min(w, x2 + margin_x) / w, "left", min(w, x2 + margin_x)),
            (min(w, w - x1 + margin_x) / w, "right", min(w, w - x1 + margin_x)),
        ]
        loss, edge, amount = min(options, key=lambda item: item[0])
        if loss > 0.25:
            held_candidates.append({**candidate, "reason": "candidate_crop_exceeds_25_percent"})
            continue
        if edge == "top":
            top_crop = max(top_crop, amount)
        elif edge == "bottom":
            bot_crop = max(bot_crop, amount)
        elif edge == "left":
            left_crop = max(left_crop, amount)
        else:
            right_crop = max(right_crop, amount)
        accepted_candidates.append({**candidate, "selected_edge": edge, "crop_fraction": round(loss, 4),
                                    "edge_touching": edge in edges})

    # sanity: don't crop more than 25% from either side
    top_crop = min(top_crop, int(h * 0.25))
    bot_crop = min(bot_crop, int(h * 0.25))
    left_crop = min(left_crop, int(w * 0.25))
    right_crop = min(right_crop, int(w * 0.25))

    crop_width = w - left_crop - right_crop
    crop_height = h - top_crop - bot_crop
    content_loss = 1.0 - (crop_width * crop_height) / (w * h)
    risk = "high" if content_loss > 0.20 else "medium" if content_loss > 0.10 else "low"
    review_required = bool(held_candidates) or risk != "low" or not accepted_candidates

    text_pos = detection["text_position"]
    return {
        "top_crop_px": top_crop,
        "bot_crop_px": bot_crop,
        "left_crop_px": left_crop,
        "right_crop_px": right_crop,
        "text_position": text_pos,
        "crop_rect": {"x": left_crop, "y": top_crop, "width": crop_width, "height": crop_height},
        "content_loss": round(content_loss, 4),
        "risk": risk,
        "review_required": review_required,
        "accepted_candidates": accepted_candidates,
        "held_candidates": held_candidates,
        "source": "detection",
    }


def apply_target_label_filter(detection: dict, path: Path, duration: float, target: str,
                              source_w: int, source_h: int, analysis_w: int, analysis_h: int) -> dict:
    """Keep only the requested top label; preserve Logo candidates independently."""
    if not target:
        return detection
    filtered = []
    target_boxes = []
    for candidate in detection.get("overlay_candidates", []):
        if candidate["kind"] == "text_label" and candidate["box"][1] < analysis_h * 0.35:
            match = match_target_label_media(path, duration, candidate["box"], source_w, source_h,
                                              analysis_w, analysis_h, target)
            candidate = {**candidate, **match}
            if candidate["target_match_score"] >= 0.55 and candidate["target_match_rate"] >= 0.25:
                candidate["kind"] = "target_text_label"
                filtered.append(candidate)
                target_boxes.append(candidate["box"])
        elif candidate["kind"] == "logo" and ((candidate["box"][1] + candidate["box"][3]) / 2) > analysis_h * 0.65:
            filtered.append(candidate)
    detection = {**detection, "overlay_candidates": filtered}
    bottom_logos = [item["box"] for item in filtered if item["kind"] == "logo"]
    detection["logo_band"] = ([min(item[1] for item in bottom_logos), max(item[3] for item in bottom_logos)]
                               if bottom_logos else None)
    if target_boxes:
        detection["top_text_band"] = [min(item[1] for item in target_boxes), max(item[3] for item in target_boxes)]
        detection["text_position"] = "top"
    else:
        detection["top_text_band"] = None
        detection["bot_text_band"] = None
        detection["text_position"] = "none"
    return detection


# ── Main API ────────────────────────────────────────────────────

def analyze(path: Path, creator: str = "", memory_dir: Path = None,
            confirm_memory: bool = False, target_label: str = "") -> dict:
    """Full analysis: detect overlays, apply memory if available, return crop params."""
    playback = check_playability(path)
    if playback["status"] != "verified":
        return {"input": str(path.resolve()), "status": "unplayable", "playback": playback}
    frames, analysis_w, analysis_h, dur, w, h = extract_frames(path)
    detection = detect_overlays(frames, analysis_w, analysis_h)
    detection = apply_target_label_filter(detection, path, dur, target_label, w, h, analysis_w, analysis_h)
    scale_y = h / analysis_h
    for key in ("top_text_band", "bot_text_band", "logo_band"):
        if detection.get(key):
            detection[key] = [round(value * scale_y) for value in detection[key]]
    detection["overlay_candidates"] = scale_candidates(
        detection.get("overlay_candidates", []), w, h, analysis_w, analysis_h
    )
    detection["analysis_resolution"] = detection["resolution"]
    detection["resolution"] = f"{w}x{h}"
    detection["duration"] = round(dur, 1)

    fresh_crop = calculate_crop(detection, w, h)
    crop = None

    # try creator memory first
    if creator and memory_dir:
        memory = load_memory(memory_dir, creator)
        if memory:
            crop = verify_memory(detection, memory, w, h, fresh_crop)
            if crop:
                detection["used_memory"] = True

    # fall back to fresh detection
    if crop is None:
        crop = fresh_crop
        detection["used_memory"] = False

    # save for evolution
    if creator and memory_dir and confirm_memory:
        save_memory(memory_dir, creator, crop, detection)

    return {"input": str(path.resolve()), "target_label": target_label,
            "playback": playback, **detection, "crop": crop}


def make_preview(path: Path, result: dict, preview_dir: Path) -> Path:
    """Render one review image with candidate boxes and proposed crop rectangle."""
    preview_dir.mkdir(parents=True, exist_ok=True)
    filters = []
    for candidate in result.get("overlay_candidates", []):
        x1, y1, x2, y2 = candidate["box"]
        filters.append(f"drawbox=x={x1}:y={y1}:w={x2-x1}:h={y2-y1}:color=red@0.9:t=4")
    rect = result["crop"]["crop_rect"]
    filters.append(
        f"drawbox=x={rect['x']}:y={rect['y']}:w={rect['width']}:h={rect['height']}:color=lime@0.9:t=5"
    )
    output = preview_dir / f"{path.stem}__label-crop-preview.jpg"
    process = subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.1, result['duration'] * 0.5):.3f}",
        "-i", str(path), "-frames:v", "1", "-vf", ",".join(filters), str(output)
    ], capture_output=True, text=True, timeout=30)
    if process.returncode or not output.is_file():
        raise RuntimeError(process.stderr.strip() or "预览图生成失败")
    return output


# ── CLI ─────────────────────────────────────────────────────────

def cmd_analyze_video(args):
    path = Path(args.input)
    result = analyze(path, args.creator or "",
                     Path(args.memory) if args.memory else None, args.confirm_memory, args.target_label)
    if args.preview_dir and result.get("status") != "unplayable":
        result["preview"] = str(make_preview(path, result, Path(args.preview_dir)))
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_analyze_dir(args):
    src = Path(args.input_dir)
    mem = Path(args.memory) if args.memory else None
    mp4s = sorted([f for f in src.glob("*.mp4") if "__refurb" not in f.name])

    output_dir = Path(args.output_dir) if args.output_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in mp4s:
        creator = args.creator or src.name  # default: use dir name as creator
        try:
            r = analyze(f, creator, mem, args.confirm_memory, args.target_label)
            if args.preview_dir and r.get("status") not in {"failed", "unplayable"}:
                r["preview"] = str(make_preview(f, r, Path(args.preview_dir)))
        except Exception as exc:
            r = {"input": str(f.resolve()), "status": "failed", "error": str(exc)}
        results.append(r)
        if r.get("status") == "failed":
            print(f"  {f.name[:45]}... 失败: {r['error']}")
            continue
        if r.get("status") == "unplayable":
            print(f"  {f.name[:45]}... 无法播放: {r['playback'].get('error', '')[:120]}")
            continue
        crop = r["crop"]
        print(f"  {f.name[:45]}... text:{crop['text_position']} "
              f"crop:上{crop['top_crop_px']} 下{crop['bot_crop_px']} "
              f"左{crop['left_crop_px']} 右{crop['right_crop_px']}px "
              f"{'(记忆)' if r.get('used_memory') else '(检测)'}")

    # summary
    with_memory = sum(1 for r in results if r.get("used_memory"))
    print(f"\n总计: {len(results)} 个, 使用记忆: {with_memory}, 新鲜检测: {len(results)-with_memory}")
    if output_dir:
        (output_dir / "label_crop_analysis.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with (output_dir / "label_crop_screening.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["file", "playback", "status", "crop_rect", "content_loss", "risk", "review_required", "preview"])
            for item in results:
                if item.get("status") == "failed":
                    writer.writerow([Path(item["input"]).name, "failed", "failed", "", "", "", True, ""])
                    continue
                if item.get("status") == "unplayable":
                    writer.writerow([Path(item["input"]).name, "failed", "unplayable", "", "", "high", True, ""])
                    continue
                crop = item["crop"]
                writer.writerow([Path(item["input"]).name, item["playback"]["status"], "candidate",
                                 json.dumps(crop["crop_rect"]), crop["content_loss"], crop["risk"],
                                 crop["review_required"], item.get("preview", "")])


def main():
    p = argparse.ArgumentParser(description="智能标签/logo检测 — 时序共识 + 创作者记忆")
    p.add_argument("input", nargs="?", help="单个视频文件")
    p.add_argument("--input-dir", help="批量分析目录")
    p.add_argument("--creator", help="创作者名称 (用于记忆)")
    p.add_argument("--memory", help="记忆存储目录")
    p.add_argument("--report", help="单视频 JSON 报告路径")
    p.add_argument("--output-dir", help="批量 JSON/TSV 报告目录")
    p.add_argument("--preview-dir", help="输出带候选框和裁剪框的复核图")
    p.add_argument("--confirm-memory", action="store_true", help="确认已人工复核后才写入创作者记忆")
    p.add_argument("--target-label", default="", help="只保留顶部匹配该文字的候选，例如 magicbox.studio")
    args = p.parse_args()

    if args.input_dir:
        cmd_analyze_dir(args)
    elif args.input:
        cmd_analyze_video(args)
    else:
        p.error("需要 input 或 --input-dir")


if __name__ == "__main__":
    raise SystemExit(main())
