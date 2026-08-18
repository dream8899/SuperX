"""Rotate a finished MP4's file identity (MD5) via non-content metadata remux."""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .errors import InputError, PlanError, ToolError
from .media import md5_file, probe_media, require_tool
from .normalize import normalization_reasons


def _decode_to_end(path: Path) -> None:
    executable = require_tool("ffmpeg")
    process = subprocess.run(
        [executable, "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or "decode check failed"
        raise PlanError(f"MD5 轮换输出无法完整解码：{message}", code="MD5_ROTATE_DECODE_FAILED")


def rotate_md5(
    input_path: str | Path,
    output_path: str | Path,
    *,
    tag: str | None = None,
) -> dict[str, Any]:
    """Write a new MP4 whose MD5 differs from the input, without changing content.

    The video/audio streams are remuxed with ``-c copy`` while a unique non-content
    container metadata tag is added, so file bytes (and thus MD5/SHA-256) differ.
    Frames, audio samples, codecs and perceptual fingerprint are unchanged.
    """
    source = Path(input_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise InputError(f"输入不存在或不是文件：{source}", code="INPUT_NOT_FILE")
    if source == output:
        raise InputError("MD5 轮换输出不得覆盖输入", code="OUTPUT_SAME_AS_INPUT")
    if output.suffix.lower() != ".mp4":
        raise InputError("MD5 轮换输出必须使用 .mp4 扩展名", code="OUTPUT_FORMAT_INVALID")
    if output.exists():
        raise PlanError(f"输出已存在：{output}", code="OUTPUT_EXISTS")
    input_media = probe_media(source)
    reasons = normalization_reasons(input_media)
    if reasons:
        raise PlanError(
            "MD5 轮换前需先完成编码兼容化：" + "；".join(reasons),
            code="MD5_ROTATE_REQUIRES_COMPATIBLE_INPUT",
        )
    metadata_tag = tag or f"svmix-md5-rotate-v1:{uuid.uuid4().hex}"
    output.parent.mkdir(parents=True, exist_ok=True)
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
    command.extend(
        [
            "-map_metadata",
            "0",
            "-metadata",
            f"comment={metadata_tag}",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(temporary),
        ]
    )
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode:
        if temporary.exists():
            temporary.unlink()
        message = process.stderr.strip() or "ffmpeg md5 rotate failed"
        raise ToolError(f"MD5 轮换失败：{message}", code="MD5_ROTATE_FAILED")
    try:
        output_media = probe_media(temporary)
        _decode_to_end(temporary)
        if output_media.get("video_codec") != input_media.get("video_codec") or (
            input_media.get("has_audio")
            and output_media.get("audio_codec") != input_media.get("audio_codec")
        ):
            raise PlanError("MD5 轮换改变了编码兼容性", code="MD5_ROTATE_VERIFY_FAILED")
        input_md5 = md5_file(source)
        output_md5 = md5_file(temporary)
        if output_md5 == input_md5:
            raise PlanError("MD5 轮换后哈希未变化，拒绝交付", code="MD5_ROTATE_NO_CHANGE")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "md5-rotation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "md5_rotated",
        "method": "container_metadata_tag",
        "tag": metadata_tag,
        "input": {
            "path": str(source),
            "size": input_media["size"],
            "md5": input_md5,
            "sha256": input_media["sha256"],
        },
        "output": {
            "path": str(output),
            "size": output_media["size"],
            "md5": output_md5,
            "sha256": output_media["sha256"],
            "video_codec": output_media.get("video_codec"),
            "audio_codec": output_media.get("audio_codec"),
            "duration": output_media.get("duration"),
        },
        "verified": True,
    }
