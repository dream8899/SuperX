import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import NotImplementedPhaseError, PlanError, ToolError
from .md5rotate import rotate_md5
from .media import probe_media, require_tool
from .plans import validate_plan_for_apply


def _operation(plan: dict[str, Any], operation_type: str) -> dict[str, Any] | None:
    return next((item for item in plan.get("operations", []) if item.get("type") == operation_type), None)


def _bounded_number(
    params: dict[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError) as exc:
        raise PlanError(f"{key} 必须是数值", code="TRANSFORM_PARAM_INVALID") from exc
    if not minimum <= value <= maximum:
        raise PlanError(f"{key} 必须在 {minimum}–{maximum} 之间", code="TRANSFORM_PARAM_INVALID")
    return value


def _atempo_chain(speed: float) -> list[float]:
    factors = []
    remaining = speed
    while remaining > 2.0 + 1e-9:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return factors


def _composition_filters(plan: dict[str, Any]) -> list[str]:
    composition = _operation(plan, "composition") or {"mode": "preserve"}
    geometry = _operation(plan, "geometry") or {"params": {"resolution": "preserve", "fps": "preserve"}}
    mode = composition.get("mode", "preserve")
    resolution = (geometry.get("params") or {}).get("resolution", "preserve")
    filters: list[str] = []
    if mode in {"smart", "manual"}:
        raise NotImplementedPhaseError(f"composition:{mode} 需要 P1 内容感知或显式坐标执行器")
    if resolution != "preserve":
        width, height = (int(value) for value in resolution.split("x", 1))
        scale_flags = str((plan.get("encode") or {}).get("scale_flags", "bicubic"))
        if mode == "fit":
            filters.extend(
                [
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags={scale_flags}",
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
                ]
            )
        elif mode == "fill":
            filters.extend(
                [
                    f"scale={width}:{height}:force_original_aspect_ratio=increase:flags={scale_flags}",
                    f"crop={width}:{height}",
                ]
            )
        elif mode == "stretch":
            filters.append(f"scale={width}:{height}:flags={scale_flags}")
        elif mode == "preserve":
            source_width = int(plan["input"]["width"])
            source_height = int(plan["input"]["height"])
            crop = _operation(plan, "crop_reframe")
            effective_ratio = 3 / 4 if crop and (crop.get("params") or {}).get("aspect") == "3:4" else source_width / source_height
            if not math.isclose(effective_ratio, width / height, rel_tol=0.002):
                raise PlanError(
                    "目标 resolution 改变画幅比时必须显式选择 fit、fill 或 stretch",
                    code="COMPOSITION_REQUIRED",
                )
            filters.append(f"scale={width}:{height}:flags={scale_flags}")
    elif mode != "preserve":
        raise PlanError(f"composition:{mode} 需要目标 resolution", code="RESOLUTION_REQUIRED")
    fps = (geometry.get("params") or {}).get("fps", "preserve")
    if fps != "preserve":
        filters.append(f"fps={fps}")
    return filters


def _visual_filters(plan: dict[str, Any]) -> list[str]:
    filters: list[str] = []
    removal = _operation(plan, "remove_region")
    if removal:
        backend = (removal.get("params") or {}).get("backend", "ffmpeg-delogo")
        if backend != "ffmpeg-delogo":
            raise NotImplementedPhaseError(
                f"水印后端 {backend} 尚未安装/接入；当前仅执行 ffmpeg-delogo。"
                " Gemini remover 只适用于已验证的 Gemini 标准水印，复杂普通水印请使用 video-inpaint 后端。"
            )
        if removal.get("mode") != "delogo":
            raise NotImplementedPhaseError(f"remove_region:{removal.get('mode')} 尚未实现")
        for region in (removal.get("params") or {}).get("regions", []):
            filters.append(
                "delogo="
                f"x={int(region['x'])}:y={int(region['y'])}:"
                f"w={int(region['width'])}:h={int(region['height'])}:show=0"
            )

    crop = _operation(plan, "crop_reframe")
    if crop and crop.get("mode") != "off":
        params = crop.get("params") or {}
        if params.get("aspect") != "3:4":
            raise PlanError("当前只支持 crop aspect 3:4", code="CROP_ASPECT_UNSUPPORTED")
        anchor = params.get("anchor", "top")
        source_width = int(plan["input"]["width"])
        source_height = int(plan["input"]["height"])
        if source_width / source_height < 3 / 4:
            crop_height = int(source_width / (3 / 4)) // 2 * 2
            y = 0 if anchor == "top" else source_height - crop_height if anchor == "bottom" else (source_height - crop_height) // 2
            filters.append(f"crop={source_width}:{crop_height}:0:{y}")
        else:
            crop_width = int(source_height * (3 / 4)) // 2 * 2
            x = 0 if anchor == "left" else source_width - crop_width if anchor == "right" else (source_width - crop_width) // 2
            filters.append(f"crop={crop_width}:{source_height}:{x}:0")

    zoom = _operation(plan, "safe_zoom")
    if zoom and zoom.get("mode") != "off":
        params = zoom.get("params") or {}
        factor = _bounded_number(params, "factor", 1.0, 1.0, 1.25)
        anchor = params.get("anchor", "top-left")
        x_map = {"top-left": "0", "bottom-left": "0", "top-right": "iw-ow", "bottom-right": "iw-ow", "center": "(iw-ow)/2"}
        y_map = {"top-left": "0", "top-right": "0", "bottom-left": "ih-oh", "bottom-right": "ih-oh", "center": "(ih-oh)/2"}
        if anchor not in x_map:
            raise PlanError("未知 zoom anchor", code="ZOOM_ANCHOR_INVALID")
        filters.extend(
            [
                f"scale=ceil(iw*{factor:g}/2)*2:ceil(ih*{factor:g}/2)*2:flags=lanczos",
                f"crop=trunc(iw/{factor:g}/2)*2:trunc(ih/{factor:g}/2)*2:{x_map[anchor]}:{y_map[anchor]}",
            ]
        )

    filters.extend(_composition_filters(plan))
    mirror = _operation(plan, "mirror")
    if mirror and mirror.get("mode") != "off":
        filters.extend((mirror.get("params") or {}).get("ffmpeg_filters", []))

    denoise = _operation(plan, "denoise")
    if denoise and denoise.get("mode") != "off":
        params = denoise.get("params") or {}
        if params.get("algorithm", "hqdn3d") != "hqdn3d":
            raise NotImplementedPhaseError("当前只支持 hqdn3d 降噪")
        luma = _bounded_number(params, "luma_spatial", 1.0, 0.0, 10.0)
        chroma = _bounded_number(params, "chroma_spatial", 0.75, 0.0, 10.0)
        filters.append(f"hqdn3d={luma:g}:{chroma:g}:{luma * 1.5:g}:{chroma * 1.5:g}")

    color = _operation(plan, "color")
    if color and color.get("mode") != "off":
        params = color.get("params") or {}
        contrast = _bounded_number(params, "contrast", 1.0, 0.5, 2.0)
        saturation = _bounded_number(params, "saturation", 1.0, 0.0, 3.0)
        brightness = _bounded_number(params, "brightness", 0.0, -1.0, 1.0)
        gamma = _bounded_number(params, "gamma", 1.0, 0.1, 10.0)
        filters.append(
            f"eq=contrast={contrast:g}:saturation={saturation:g}:brightness={brightness:g}:gamma={gamma:g}"
        )
        temperature = _bounded_number(params, "temperature_shift", 0.0, -0.5, 0.5)
        if temperature:
            filters.append(f"colorbalance=rs={temperature:g}:bs={-temperature:g}")

    look_filter = _operation(plan, "filter")
    if look_filter and look_filter.get("mode") != "off":
        if look_filter.get("mode") == "custom":
            raise NotImplementedPhaseError("custom filter graph 尚未开放执行；使用版本化 preset")
        preset = look_filter.get("preset")
        preset_filters = {
            "cinematic": "eq=contrast=1.06:saturation=0.92:gamma=0.98",
            "soft": "gblur=sigma=0.4",
            "vintage": "curves=preset=vintage",
            "monochrome": "hue=s=0",
        }
        if preset not in preset_filters:
            raise PlanError(f"未知 filter preset：{preset}", code="FILTER_PRESET_INVALID")
        filters.append(preset_filters[preset])

    sharpen = _operation(plan, "sharpen")
    if sharpen and sharpen.get("mode") != "off":
        params = sharpen.get("params") or {}
        if params.get("algorithm", "unsharp") != "unsharp":
            raise NotImplementedPhaseError("当前只支持 unsharp 锐化")
        amount = _bounded_number(params, "amount", 0.35, 0.0, 2.0)
        filters.append(f"unsharp=5:5:{amount:g}:5:5:0")
    return filters


def _build_filter_graph(plan: dict[str, Any]) -> tuple[str, bool, list[str]]:
    trim = _operation(plan, "trim")
    speed_operation = _operation(plan, "speed") or {"mode": "off", "params": {"factor": 1.0}}
    speed = float((speed_operation.get("params") or {}).get("factor", 1.0))
    start = float((trim.get("params") or {}).get("start", 0.0)) if trim else 0.0
    end = float((trim.get("params") or {}).get("end")) if trim else None
    video_filters = []
    if end is not None:
        video_filters.append(f"trim=start={start:.6f}:end={end:.6f}")
    video_filters.append(f"setpts=(PTS-STARTPTS)/{speed:.12g}")
    video_filters.extend(_visual_filters(plan))
    graph_parts = [f"[0:v:0]{','.join(video_filters)}[v]"]

    has_audio = bool(plan["input"].get("has_audio"))
    if has_audio:
        audio_filters = []
        if end is not None:
            audio_filters.append(f"atrim=start={start:.6f}:end={end:.6f}")
        audio_filters.append("asetpts=PTS-STARTPTS")
        if not math.isclose(speed, 1.0):
            audio_filters.extend(f"atempo={factor:.12g}" for factor in _atempo_chain(speed))
        graph_parts.append(f"[0:a:0]{','.join(audio_filters)}[a]")

    executed = [
        operation["type"]
        for operation in plan["operations"]
        if operation["type"] == "trim"
        or operation["type"] == "remove_region"
        or operation.get("mode") not in {"off", "preserve"}
        or operation["type"] == "geometry" and operation.get("params") != {"resolution": "preserve", "fps": "preserve"}
    ]
    return ";".join(graph_parts), has_audio, executed


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
        raise PlanError(f"输出无法完整解码：{message}", code="OUTPUT_DECODE_FAILED")


def _verify_temporary_output(
    plan: dict[str, Any],
    temporary_output: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    media = probe_media(temporary_output)
    _decode_to_end(temporary_output)
    checks: list[dict[str, Any]] = [
        {"name": "decodable_to_end", "status": "pass"},
        {"name": "video_stream", "status": "pass"},
    ]
    expected = plan.get("expected") or {}
    input_media = plan.get("input") or {}
    expected_resolution = expected.get("resolution", "preserve")
    if expected_resolution == "preserve":
        resolution_ok = media.get("width") == input_media.get("width") and media.get("height") == input_media.get("height")
    else:
        width, height = (int(value) for value in expected_resolution.split("x", 1))
        resolution_ok = media.get("width") == width and media.get("height") == height
    checks.append({"name": "resolution", "status": "pass" if resolution_ok else "fail"})
    audio_ok = bool(media.get("has_audio")) == bool(expected.get("has_audio"))
    checks.append({"name": "audio_stream", "status": "pass" if audio_ok else "fail"})
    video_duration = media.get("video_duration")
    audio_duration = media.get("audio_duration")
    if audio_ok and video_duration is not None and audio_duration is not None:
        sync_tolerance = max(float(expected.get("duration_tolerance", 0.12)), 1.0 / 24.0)
        sync_delta = abs(float(video_duration) - float(audio_duration))
        checks.append(
            {
                "name": "av_sync_duration",
                "status": "pass" if sync_delta <= sync_tolerance else "fail",
                "video_duration": video_duration,
                "audio_duration": audio_duration,
                "delta": sync_delta,
                "tolerance": sync_tolerance,
            }
        )
    expected_duration = expected.get("duration")
    actual_duration = media.get("duration")
    if expected_duration is not None and actual_duration is not None:
        tolerance = float(expected.get("duration_tolerance", 0.12))
        duration_ok = abs(float(actual_duration) - float(expected_duration)) <= tolerance
        checks.append(
            {
                "name": "duration",
                "status": "pass" if duration_ok else "fail",
                "expected": expected_duration,
                "actual": actual_duration,
                "tolerance": tolerance,
            }
        )
    failed = [check["name"] for check in checks if check["status"] != "pass"]
    if failed:
        raise PlanError(
            f"临时输出验证失败：{', '.join(failed)}；诊断文件保留在 {temporary_output}",
            code="OUTPUT_VALIDATION_FAILED",
        )
    return media, checks


def execute_plan(plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan_for_apply(plan)
    source = Path(plan["input"]["path"]).expanduser().resolve()
    output = Path(plan["output"]["path"]).expanduser().resolve()
    if output.exists():
        raise PlanError(f"输出已存在，拒绝覆盖：{output}", code="OUTPUT_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.stem}.",
        suffix=".partial.mp4",
    )
    os.close(file_descriptor)
    temporary_output = Path(temporary_name)
    temporary_output.unlink()

    filter_graph, has_audio, executed = _build_filter_graph(plan)
    encode = plan.get("encode") or {}
    if encode.get("video_codec") != "libx264":
        raise PlanError("当前执行器只支持 libx264", code="VIDEO_CODEC_UNSUPPORTED")
    executable = require_tool("ffmpeg")
    command = [
        executable,
        "-hide_banner",
        "-v",
        "error",
        "-n",
        "-i",
        str(source),
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
    ]
    if has_audio:
        command.extend(["-map", "[a]"])
    command.extend(
        [
            "-c:v",
            "libx264",
            "-crf",
            str(encode.get("crf", 18)),
            "-preset",
            str(encode.get("preset", "medium")),
            "-pix_fmt",
            str(encode.get("pixel_format", "yuv420p")),
        ]
    )
    if has_audio:
        command.extend(
            [
                "-c:a",
                str(encode.get("audio_codec", "aac")),
                "-b:a",
                str(encode.get("audio_bitrate", "192k")),
            ]
        )
    if encode.get("faststart", True):
        command.extend(["-movflags", "+faststart"])
    command.append(str(temporary_output))

    started_at = datetime.now(timezone.utc)
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        message = process.stderr.strip() or "ffmpeg render failed"
        raise ToolError(
            f"FFmpeg 渲染失败：{message}；临时路径 {temporary_output}",
            code="FFMPEG_RENDER_FAILED",
        )
    md5_rotate_info = None
    md5_operation = _operation(plan, "md5_rotate")
    if md5_operation and md5_operation.get("mode") != "off":
        file_descriptor, rotated_name = tempfile.mkstemp(
            dir=output.parent,
            prefix=f".{output.stem}.",
            suffix=".rotated.mp4",
        )
        os.close(file_descriptor)
        rotated_temporary = Path(rotated_name)
        rotated_temporary.unlink()
        md5_rotate_info = rotate_md5(temporary_output, rotated_temporary)
        output_media, checks = _verify_temporary_output(plan, rotated_temporary)
        checks.append(
            {
                "name": "md5_rotated",
                "status": "pass",
                "input_md5": md5_rotate_info["input"]["md5"],
                "output_md5": md5_rotate_info["output"]["md5"],
                "tag": md5_rotate_info["tag"],
            }
        )
        output_media["path"] = str(output)
        temporary_output.unlink()
        delivery_source = rotated_temporary
    else:
        output_media, checks = _verify_temporary_output(plan, temporary_output)
        delivery_source = temporary_output
    try:
        os.link(delivery_source, output)
    except FileExistsError as exc:
        raise PlanError(f"输出在交付前已出现，拒绝覆盖：{output}", code="OUTPUT_EXISTS") from exc
    except OSError as exc:
        raise ToolError(f"无法交付验证后的输出：{exc}", code="OUTPUT_DELIVERY_FAILED") from exc
    delivery_source.unlink()
    output_media["path"] = str(output)
    completed_at = datetime.now(timezone.utc)
    result = {
        "schema_version": plan["schema_version"],
        "kind": "video-execution",
        "status": "verified",
        "job_id": plan["job_id"],
        "plan_hash": plan["plan_hash"],
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "elapsed_seconds": round((completed_at - started_at).total_seconds(), 3),
        "operations_executed": executed,
        "output": output_media,
        "checks": checks,
    }
    if md5_rotate_info:
        result["md5_rotate"] = {
            "method": md5_rotate_info["method"],
            "tag": md5_rotate_info["tag"],
            "input_md5": md5_rotate_info["input"]["md5"],
            "output_md5": md5_rotate_info["output"]["md5"],
            "input_sha256": md5_rotate_info["input"]["sha256"],
            "output_sha256": md5_rotate_info["output"]["sha256"],
        }
    if plan.get("lineage"):
        result["lineage"] = plan["lineage"]
    return result
