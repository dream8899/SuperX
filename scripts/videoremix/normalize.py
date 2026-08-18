"""Normalize downloaded media to a QuickTime-compatible H.264/AAC MP4."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import InputError, PlanError, ToolError
from .media import probe_media, require_tool


def _is_mp4_container(value: str | None) -> bool:
    return bool(value) and any(part in value.split(",") for part in ("mp4", "m4a", "3gp", "3g2", "mj2"))


def normalization_reasons(media: dict[str, Any]) -> list[str]:
    reasons = []
    video_codec = str(media.get("video_codec") or "").lower()
    video_tag = str(media.get("video_codec_tag") or "").lower()
    audio_codec = str(media.get("audio_codec") or "").lower()
    if video_codec in {"vp9", "vp09"} or video_tag in {"vp9", "vp09"}:
        reasons.append("VP9/VP09 视频流在 QuickTime 中不兼容")
    elif video_codec != "h264":
        reasons.append(f"视频编码为 {video_codec or 'unknown'}，目标要求 H.264")
    if media.get("has_audio") and audio_codec != "aac":
        reasons.append(f"音频编码为 {audio_codec or 'unknown'}，目标要求 AAC")
    if not _is_mp4_container(media.get("container")):
        reasons.append(f"容器为 {media.get('container') or 'unknown'}，目标要求 MP4")
    return reasons


def _decode_to_end(path: Path) -> None:
    executable = require_tool("ffmpeg")
    process = subprocess.run(
        [executable, "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        message = process.stderr.strip() or "decode check failed"
        raise PlanError(f"兼容化输出无法完整解码：{message}", code="NORMALIZE_DECODE_FAILED")


def normalize_download(input_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise InputError(f"输入不存在或不是文件：{source}", code="INPUT_NOT_FILE")
    if source == output:
        raise InputError("兼容化输出不得覆盖下载源文件", code="OUTPUT_SAME_AS_INPUT")
    if output.suffix.lower() != ".mp4":
        raise InputError("兼容化输出必须使用 .mp4 扩展名", code="OUTPUT_FORMAT_INVALID")
    if output.exists():
        raise PlanError(f"输出已存在：{output}", code="OUTPUT_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)

    input_media = probe_media(source)
    reasons = normalization_reasons(input_media)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp.mp4")
    if temporary.exists():
        temporary.unlink()
    executable = require_tool("ffmpeg")
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
    ]
    if input_media.get("has_audio"):
        command.extend(["-map", "0:a:0"])
    command.extend(["-map_metadata", "0"])
    if reasons:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        )
    else:
        command.extend(["-c", "copy"])
    command.extend(["-movflags", "+faststart", "-f", "mp4", str(temporary)])
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode:
        if temporary.exists():
            temporary.unlink()
        message = process.stderr.strip() or "ffmpeg normalize failed"
        raise ToolError(f"H.264/AAC 兼容化失败：{message}", code="NORMALIZE_FAILED")
    try:
        _decode_to_end(temporary)
        output_media = probe_media(temporary)
        expected_audio = not input_media.get("has_audio") or output_media.get("audio_codec") == "aac"
        verified = (
            output_media.get("video_codec") == "h264"
            and expected_audio
            and _is_mp4_container(output_media.get("container"))
        )
        if not verified:
            raise PlanError(
                f"兼容化后编码不符合要求：video={output_media.get('video_codec')}, "
                f"audio={output_media.get('audio_codec')}, container={output_media.get('container')}",
                code="NORMALIZE_VERIFY_FAILED",
            )
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "status": "converted" if reasons else "already_compatible",
        "input": input_media,
        "output": output_media,
        "output_path": str(output),
        "conversion_required": bool(reasons),
        "reasons": reasons,
        "video_target": "h264",
        "audio_target": "aac" if input_media.get("has_audio") else None,
        "container_target": "mp4",
        "verified": True,
    }
