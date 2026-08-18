import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import InputError, ToolError


def resolve_input_file(path: str | Path) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise InputError(f"输入不存在：{source}", code="INPUT_NOT_FOUND")
    if not source.is_file():
        raise InputError(f"输入不是文件：{source}", code="INPUT_NOT_FILE")
    return source


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise ToolError(f"缺少必需工具：{name}", code="TOOL_MISSING")
    return executable


def tool_version(name: str) -> str:
    executable = require_tool(name)
    process = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        return "unknown"
    return process.stdout.splitlines()[0].strip() if process.stdout else "unknown"


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    source = resolve_input_file(path)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def md5_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    source = resolve_input_file(path)
    digest = hashlib.md5()
    with source.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _duration(format_data: dict[str, Any], streams: list[dict[str, Any]]) -> float | None:
    candidates = [format_data.get("duration")]
    candidates.extend(stream.get("duration") for stream in streams)
    for candidate in candidates:
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _stream_duration(stream: dict[str, Any] | None) -> float | None:
    if not stream:
        return None
    try:
        value = float(stream.get("duration"))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def probe_media(path: str | Path) -> dict[str, Any]:
    source = resolve_input_file(path)
    executable = require_tool("ffprobe")
    process = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or "ffprobe failed"
        raise ToolError(f"媒体探测失败：{message}", code="FFPROBE_FAILED")
    try:
        raw = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError("ffprobe 返回了无效 JSON", code="FFPROBE_INVALID_JSON") from exc

    streams = raw.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if not video:
        raise InputError(f"输入没有视频轨：{source}", code="VIDEO_STREAM_MISSING")

    rotation = None
    tags = video.get("tags") or {}
    if "rotate" in tags:
        try:
            rotation = int(tags["rotate"])
        except (TypeError, ValueError):
            rotation = None
    for side_data in video.get("side_data_list") or []:
        if rotation is None and "rotation" in side_data:
            try:
                rotation = int(side_data["rotation"])
            except (TypeError, ValueError):
                pass

    format_data = raw.get("format") or {}
    return {
        "path": str(source),
        "size": source.stat().st_size,
        "sha256": sha256_file(source),
        "duration": _duration(format_data, streams),
        "video_duration": _stream_duration(video),
        "audio_duration": _stream_duration(audio),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": video.get("avg_frame_rate") or video.get("r_frame_rate") or "unknown",
        "avg_frame_rate": video.get("avg_frame_rate"),
        "r_frame_rate": video.get("r_frame_rate"),
        "time_base": video.get("time_base"),
        "start_time": video.get("start_time", format_data.get("start_time")),
        "rotation": rotation,
        "video_codec": video.get("codec_name"),
        "video_codec_tag": video.get("codec_tag_string"),
        "pixel_format": video.get("pix_fmt"),
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_sample_rate": audio.get("sample_rate") if audio else None,
        "container": format_data.get("format_name"),
    }
