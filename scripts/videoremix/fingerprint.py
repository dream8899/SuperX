"""Dependency-light video perceptual fingerprints and candidate grouping."""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path
from typing import Any

from .errors import ToolError
from .media import probe_media, require_tool, sha256_file


FINGERPRINT_VERSION = "average-hash-v1"
HASH_WIDTH = 32
HASH_HEIGHT = 32
SAMPLE_FRACTIONS = (0.05, 0.25, 0.50, 0.75, 0.95)


def _sample_times(duration: float | None) -> list[float]:
    if duration is None or duration <= 0:
        return [0.0]
    return [max(0.0, min(duration - 0.02, duration * fraction)) for fraction in SAMPLE_FRACTIONS]


def _pack_average_hash(raw: bytes) -> str:
    expected = HASH_WIDTH * HASH_HEIGHT
    if len(raw) != expected:
        raise ToolError(
            f"关键帧像素数量异常：期望 {expected}，实际 {len(raw)}",
            code="FINGERPRINT_FRAME_INVALID",
        )
    mean = sum(raw) / len(raw)
    packed = bytearray()
    value = 0
    for index, pixel in enumerate(raw):
        value = (value << 1) | int(pixel >= mean)
        if index % 8 == 7:
            packed.append(value)
            value = 0
    return bytes(packed).hex()


def _frame_hash(path: Path, timestamp: float) -> str:
    executable = require_tool("ffmpeg")
    process = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(path),
            "-vf",
            f"scale={HASH_WIDTH}:{HASH_HEIGHT}:flags=bilinear,format=gray",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if process.returncode:
        message = process.stderr.decode(errors="replace").strip() or "frame extraction failed"
        raise ToolError(f"感知指纹关键帧提取失败：{message}", code="FINGERPRINT_FRAME_FAILED")
    return _pack_average_hash(process.stdout)


def fingerprint_media(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    media = probe_media(source)
    times = _sample_times(media.get("duration"))
    return {
        "algorithm": FINGERPRINT_VERSION,
        "sha256": sha256_file(source),
        "path": str(source),
        "duration": media.get("duration"),
        "width": media.get("width"),
        "height": media.get("height"),
        "samples": [{"fraction": fraction, "time": timestamp, "hash": _frame_hash(source, timestamp)} for fraction, timestamp in zip(SAMPLE_FRACTIONS if len(times) == len(SAMPLE_FRACTIONS) else (0.5,), times)],
    }


def _hamming(left: str, right: str) -> float:
    left_bytes = bytes.fromhex(left)
    right_bytes = bytes.fromhex(right)
    if len(left_bytes) != len(right_bytes):
        return 1.0
    different = sum((a ^ b).bit_count() for a, b in zip(left_bytes, right_bytes))
    return different / (len(left_bytes) * 8)


def compare_fingerprints(left: dict[str, Any], right: dict[str, Any], threshold: float) -> dict[str, Any]:
    if left.get("sha256") == right.get("sha256"):
        return {
            "classification": "exact",
            "similarity": 1.0,
            "confidence": 1.0,
            "evidence": ["sha256_equal"],
        }
    left_samples = left.get("samples") or []
    right_samples = right.get("samples") or []
    count = min(len(left_samples), len(right_samples))
    visual_similarity = 0.0
    if count:
        visual_similarity = sum(
            1.0 - _hamming(left_samples[index]["hash"], right_samples[index]["hash"])
            for index in range(count)
        ) / count
    left_duration = left.get("duration")
    right_duration = right.get("duration")
    if left_duration and right_duration:
        duration_similarity = max(0.0, 1.0 - abs(left_duration - right_duration) / max(left_duration, right_duration))
    else:
        duration_similarity = 0.5
    score = round(visual_similarity * 0.85 + duration_similarity * 0.15, 6)
    if score >= max(threshold, 0.92):
        classification = "likely"
    elif score >= threshold:
        classification = "possible"
    else:
        classification = "distinct"
    return {
        "classification": classification,
        "similarity": score,
        "confidence": score,
        "evidence": ["sampled_average_hash", "duration_similarity"],
    }


def build_candidate_report(paths: list[Path], threshold: float = 0.86) -> dict[str, Any]:
    fingerprints = [fingerprint_media(path) for path in paths]
    exact_groups: dict[str, list[int]] = {}
    for index, item in enumerate(fingerprints):
        exact_groups.setdefault(item["sha256"], []).append(index)
    exact_groups_output = [
        {"classification": "exact", "sha256": digest, "items": [fingerprints[index]["path"] for index in indexes]}
        for digest, indexes in exact_groups.items()
        if len(indexes) > 1
    ]
    candidates = []
    parent = list(range(len(fingerprints)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in itertools.combinations(range(len(fingerprints)), 2):
        comparison = compare_fingerprints(fingerprints[left], fingerprints[right], threshold)
        if comparison["classification"] in {"exact", "likely", "possible"}:
            union(left, right)
            candidates.append({
                "left": fingerprints[left]["path"],
                "right": fingerprints[right]["path"],
                **comparison,
            })
    groups: dict[int, list[str]] = {}
    for index, item in enumerate(fingerprints):
        root = find(index)
        groups.setdefault(root, []).append(item["path"])
    candidate_groups = [
        {"group_id": f"candidate-{number:03d}", "classification": "candidate", "items": items}
        for number, items in enumerate(groups.values(), 1)
        if len(items) > 1
    ]
    return {
        "kind": "perceptual-deduplication",
        "algorithm": FINGERPRINT_VERSION,
        "threshold": threshold,
        "files_scanned": len(fingerprints),
        "items": fingerprints,
        "exact_groups": exact_groups_output,
        "similar_candidates": candidates,
        "candidate_groups": candidate_groups,
        "deleted": [],
        "uncertainties": [
            "感知指纹用于本地分类和人工复核，不代表平台判定，也不会修改或伪造视频指纹。",
            "当前为视频帧 dHash 风格采样，未包含音频 Chromaprint。",
        ],
    }
