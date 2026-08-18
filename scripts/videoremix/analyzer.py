import array
import math
import statistics
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import ToolError
from .media import require_tool


@dataclass(frozen=True)
class Profile:
    tail_fraction: float
    max_tail_seconds: float
    min_junk_seconds: float
    sample_fps: int = 4


PROFILES = {
    "generic": Profile(0.18, 15.0, 1.25),
    "douyin": Profile(0.25, 20.0, 0.8),
    "tiktok": Profile(0.22, 18.0, 0.9),
    "instagram": Profile(0.16, 12.0, 1.0),
    "youtube-short": Profile(0.14, 12.0, 1.0),
}


@dataclass
class FrameMetric:
    time: float
    mean: float
    contrast: float
    sharpness: float
    difference: float | None
    audio_db: float | None
    score: int = 0
    reasons: tuple[str, ...] = ()


def _run_ffmpeg(arguments: list[str], *, error_code: str) -> bytes:
    executable = require_tool("ffmpeg")
    process = subprocess.run(
        [executable, *arguments],
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip() or "ffmpeg failed"
        raise ToolError(f"FFmpeg 分析失败：{message}", code=error_code)
    return process.stdout


def _frame_stats(frame: bytes, side: int) -> tuple[float, float, float]:
    mean = statistics.fmean(frame)
    contrast = math.sqrt(statistics.fmean((value - mean) ** 2 for value in frame))
    edges: list[int] = []
    for y in range(1, side - 1):
        row = y * side
        for x in range(1, side - 1):
            position = row + x
            horizontal = abs(frame[position - 1] - 2 * frame[position] + frame[position + 1])
            vertical = abs(frame[position - side] - 2 * frame[position] + frame[position + side])
            edges.append(horizontal + vertical)
    return mean, contrast, statistics.fmean(edges)


def _audio_levels(path: Path, start: float, duration: float, fps: int) -> list[float] | None:
    rate = 8000
    try:
        raw = _run_ffmpeg(
            [
                "-v",
                "error",
                "-ss",
                f"{start:.6f}",
                "-t",
                f"{duration:.6f}",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(rate),
                "-f",
                "s16le",
                "-",
            ],
            error_code="AUDIO_ANALYSIS_FAILED",
        )
    except ToolError:
        return None
    samples = array.array("h")
    samples.frombytes(raw)
    window = max(1, rate // fps)
    levels: list[float] = []
    for offset in range(0, len(samples), window):
        values = samples[offset : offset + window]
        if not values:
            break
        rms = math.sqrt(sum(value * value for value in values) / len(values))
        levels.append(20 * math.log10(max(rms, 1) / 32768))
    return levels


def sample_tail(
    path: Path,
    duration: float,
    has_audio: bool,
    profile: Profile,
) -> tuple[float, list[FrameMetric]]:
    tail_duration = min(profile.max_tail_seconds, max(6.0, duration * profile.tail_fraction))
    tail_duration = min(duration, tail_duration)
    start = max(0.0, duration - tail_duration)
    side = 96
    raw = _run_ffmpeg(
        [
            "-v",
            "error",
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{tail_duration:.6f}",
            "-i",
            str(path),
            "-an",
            "-vf",
            f"fps={profile.sample_fps},scale={side}:{side}:flags=area",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ],
        error_code="VIDEO_ANALYSIS_FAILED",
    )
    frame_size = side * side
    frames = [raw[index : index + frame_size] for index in range(0, len(raw) - frame_size + 1, frame_size)]
    audio = _audio_levels(path, start, tail_duration, profile.sample_fps) if has_audio else [-120.0] * len(frames)
    metrics: list[FrameMetric] = []
    previous: bytes | None = None
    for index, frame in enumerate(frames):
        mean, contrast, sharpness = _frame_stats(frame, side)
        difference = None if previous is None else statistics.fmean(abs(a - b) for a, b in zip(frame, previous))
        audio_db = audio[index] if audio and index < len(audio) else None
        metrics.append(
            FrameMetric(
                time=start + index / profile.sample_fps,
                mean=mean,
                contrast=contrast,
                sharpness=sharpness,
                difference=difference,
                audio_db=audio_db,
            )
        )
        previous = frame
    return start, metrics


def classify_metrics(metrics: list[FrameMetric]) -> None:
    for metric in metrics:
        reasons: list[str] = []
        score = 0
        silent = metric.audio_db is not None and metric.audio_db < -42
        if metric.mean < 24 and metric.contrast < 24:
            score += 4
            reasons.append("dark")
        if metric.contrast < 7 and silent:
            score += 2
            reasons.append("flat")
        if metric.difference is not None and metric.difference < 1.1 and silent:
            score += 2
            reasons.append("duplicate_or_static")
        if metric.sharpness < 5.0 and metric.contrast < 28 and silent:
            score += 1
            reasons.append("low_detail_or_blurry")
        if silent:
            score += 1
            reasons.append("silent")
        metric.score = score
        metric.reasons = tuple(reasons)


def find_tail_cut(
    metrics: list[FrameMetric],
    duration: float,
    profile: Profile,
) -> tuple[float | None, list[str]]:
    if not metrics:
        return None, []
    classify_metrics(metrics)
    last_good = len(metrics) - 1
    while last_good >= 0 and metrics[last_good].score >= 3:
        last_good -= 1
    if last_good >= 0 and last_good + 2 < len(metrics) and metrics[last_good].score < 3:
        earlier = last_good - 1
        if earlier >= 0 and metrics[earlier].score >= 3:
            last_good -= 1
            while last_good >= 0 and metrics[last_good].score >= 3:
                last_good -= 1
    first_junk = last_good + 1
    if first_junk >= len(metrics):
        return None, []
    cut_at = metrics[first_junk].time
    if duration - cut_at < profile.min_junk_seconds:
        return None, []
    reasons = sorted({reason for metric in metrics[first_junk:] for reason in metric.reasons})
    return max(0.0, cut_at), reasons


def analyze_tail(path: str | Path, media: dict[str, Any], profile_name: str) -> dict[str, Any]:
    profile = PROFILES[profile_name]
    duration = media.get("duration")
    if duration is None or duration <= 0:
        raise ToolError("无法确定媒体时长，不能分析废片尾", code="DURATION_UNKNOWN")
    source = Path(path).expanduser().resolve()
    start, metrics = sample_tail(source, duration, bool(media.get("has_audio")), profile)
    cut_at, reasons = find_tail_cut(metrics, duration, profile)
    removed_seconds = 0.0 if cut_at is None else max(0.0, duration - cut_at)
    confidence = 0.0
    if cut_at is not None:
        confidence = 0.92 if "dark" in reasons and "silent" in reasons else 0.8 if len(reasons) >= 2 else 0.65
    intervals = []
    if cut_at is not None:
        intervals.append(
            {
                "type": "junk_tail",
                "start": round(cut_at, 3),
                "end": round(duration, 3),
                "duration": round(removed_seconds, 3),
                "evidence": reasons,
                "confidence": confidence,
                "recommendation": "review_then_trim",
                "approved": False,
            }
        )
    return {
        "profile": profile_name,
        "profile_settings": asdict(profile),
        "tail_scan_start": round(start, 3),
        "suggested_cut_at": None if cut_at is None else round(cut_at, 3),
        "removed_tail_seconds": round(removed_seconds, 3),
        "evidence": reasons,
        "suggested_intervals": intervals,
        "samples": [
            {
                "time": round(metric.time, 3),
                "mean": round(metric.mean, 2),
                "contrast": round(metric.contrast, 2),
                "sharpness": round(metric.sharpness, 2),
                "difference": None if metric.difference is None else round(metric.difference, 2),
                "audio_db": None if metric.audio_db is None else round(metric.audio_db, 2),
                "score": metric.score,
                "reasons": list(metric.reasons),
            }
            for metric in metrics
        ],
    }
