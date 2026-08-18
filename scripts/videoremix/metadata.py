"""Safe metadata sanitization for local asset catalogs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import InputError, PlanError, ToolError
from .media import probe_media, require_tool
from .normalize import normalization_reasons


def sanitize_metadata(input_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise InputError(f"输入不存在或不是文件：{source}", code="INPUT_NOT_FILE")
    if source == output:
        raise InputError("元数据规范化不得覆盖源文件", code="OUTPUT_SAME_AS_INPUT")
    if output.suffix.lower() != ".mp4":
        raise InputError("元数据规范化输出必须使用 .mp4 扩展名", code="OUTPUT_FORMAT_INVALID")
    if output.exists():
        raise PlanError(f"输出已存在：{output}", code="OUTPUT_EXISTS")
    input_media = probe_media(source)
    reasons = normalization_reasons(input_media)
    if reasons:
        raise PlanError(
            "元数据规范化前需先完成编码兼容化：" + "；".join(reasons),
            code="METADATA_REQUIRES_COMPATIBLE_INPUT",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp.mp4")
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
    command.extend(["-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(temporary)])
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode:
        if temporary.exists():
            temporary.unlink()
        raise ToolError(process.stderr.strip() or "metadata sanitize failed", code="METADATA_SANITIZE_FAILED")
    try:
        output_media = probe_media(temporary)
        if output_media.get("video_codec") != "h264" or (input_media.get("has_audio") and output_media.get("audio_codec") != "aac"):
            raise PlanError("元数据规范化改变了编码兼容性", code="METADATA_VERIFY_FAILED")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "status": "metadata_sanitized",
        "input": input_media,
        "output": output_media,
        "output_path": str(output),
        "source_hash": input_media["sha256"],
        "output_hash": output_media["sha256"],
        "sanitized_fields": ["format_tags", "stream_tags", "chapters"],
        "verified": True,
    }
