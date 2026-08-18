import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, SKILL_VERSION
from .errors import InputError, PlanError
from .media import sha256_file, tool_version

COMPOSITION_MODES = ("preserve", "fit", "fill", "smart", "stretch", "manual")
COLOR_CHOICES = ("off", "auto", "natural", "warm", "vivid", "custom")
FILTER_CHOICES = ("off", "cinematic", "soft", "vintage", "monochrome", "custom")
DETAIL_CHOICES = ("off", "auto", "light", "medium", "custom")
FLIP_CHOICES = ("off", "horizontal", "vertical", "both")

COLOR_PARAMS = {
    "natural": {"contrast": 1.03, "saturation": 1.03},
    "warm": {"temperature_shift": 0.08, "saturation": 1.04},
    "vivid": {"contrast": 1.08, "saturation": 1.12},
}
FILTER_PARAMS = {
    "cinematic": {"look": "cinematic-v1", "strength": 0.35},
    "soft": {"look": "soft-v1", "strength": 0.25},
    "vintage": {"look": "vintage-v1", "strength": 0.35},
    "monochrome": {"look": "monochrome-v1", "strength": 1.0},
}
DETAIL_PARAMS = {
    "denoise": {
        "light": {"algorithm": "hqdn3d", "luma_spatial": 1.0, "chroma_spatial": 0.75},
        "medium": {"algorithm": "hqdn3d", "luma_spatial": 2.0, "chroma_spatial": 1.5},
    },
    "sharpen": {
        "light": {"algorithm": "unsharp", "amount": 0.35},
        "medium": {"algorithm": "unsharp", "amount": 0.65},
    },
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_plan_hash(plan: dict[str, Any]) -> str:
    normalized = copy.deepcopy(plan)
    normalized.pop("plan_hash", None)
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def validate_plan_structure(plan: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "kind",
        "job_id",
        "input",
        "operations",
        "preview",
        "output",
        "plan_hash",
    }
    missing = sorted(required.difference(plan))
    if missing:
        raise PlanError(f"计划缺少字段：{', '.join(missing)}", code="PLAN_FIELD_MISSING")
    if plan.get("schema_version") != SCHEMA_VERSION or plan.get("kind") != "video-remix-plan":
        raise PlanError("不支持的 schema_version 或 kind", code="PLAN_SCHEMA_UNSUPPORTED")
    if not isinstance(plan.get("operations"), list) or not plan["operations"]:
        raise PlanError("operations 必须是非空 array", code="PLAN_OPERATIONS_INVALID")
    operation_fields = {"type", "mode", "params", "risk", "requires_preview", "approved"}
    for index, operation in enumerate(plan["operations"]):
        if not isinstance(operation, dict) or not operation_fields.issubset(operation):
            raise PlanError(f"operations[{index}] 结构无效", code="PLAN_OPERATION_INVALID")
        if operation["risk"] not in {"low", "medium", "high"}:
            raise PlanError(f"operations[{index}].risk 无效", code="PLAN_OPERATION_INVALID")
    if not isinstance(plan.get("preview"), dict) or "status" not in plan["preview"]:
        raise PlanError("preview 结构无效", code="PLAN_PREVIEW_INVALID")
    output_path = (plan.get("output") or {}).get("path")
    if not isinstance(output_path, str) or not output_path:
        raise PlanError("output.path 结构无效", code="PLAN_OUTPUT_INVALID")


def verify_plan_hash(plan: dict[str, Any]) -> None:
    validate_plan_structure(plan)
    actual = plan.get("plan_hash")
    expected = compute_plan_hash(plan)
    if not actual or actual != expected:
        raise PlanError("plan_hash 不匹配，计划可能已被修改", code="PLAN_HASH_MISMATCH")


def parse_resolution(value: str) -> str:
    if value == "preserve":
        return value
    match = re.fullmatch(r"([1-9]\d{1,4})x([1-9]\d{1,4})", value)
    if not match:
        raise InputError("resolution 必须是 preserve 或 WIDTHxHEIGHT", code="INVALID_RESOLUTION")
    width, height = (int(item) for item in match.groups())
    if width > 16384 or height > 16384:
        raise InputError("resolution 超出 16384 像素上限", code="INVALID_RESOLUTION")
    return value


def parse_fps(value: str) -> str:
    if value == "preserve":
        return value
    try:
        number = float(value)
    except ValueError as exc:
        raise InputError("fps 必须是 preserve 或 1–240 的数值", code="INVALID_FPS") from exc
    if not 1 <= number <= 240:
        raise InputError("fps 必须在 1–240 之间", code="INVALID_FPS")
    return f"{number:g}"


def parse_region(value: str, width: int, height: int) -> dict[str, int]:
    parts = value.split(":")
    if len(parts) != 4:
        raise InputError("region 必须是 X:Y:W:H，支持百分比", code="INVALID_REGION")

    def coordinate(raw: str, total: int) -> int:
        try:
            return round(total * float(raw[:-1]) / 100) if raw.endswith("%") else int(raw)
        except ValueError as exc:
            raise InputError(f"region 坐标无效：{value}", code="INVALID_REGION") from exc

    x = coordinate(parts[0], width)
    y = coordinate(parts[1], height)
    region_width = coordinate(parts[2], width)
    region_height = coordinate(parts[3], height)
    if (
        x < 0
        or y < 0
        or region_width < 3
        or region_height < 3
        or x + region_width > width
        or y + region_height > height
    ):
        raise InputError(f"region 超出 {width}x{height}：{value}", code="INVALID_REGION")
    return {"x": x, "y": y, "width": region_width, "height": region_height}


def _selection(
    operation_type: str,
    choice: str,
    *,
    presets: dict[str, dict[str, Any]],
    custom_params: dict[str, Any] | None = None,
    medium_presets: set[str] | None = None,
) -> dict[str, Any]:
    medium_presets = medium_presets or set()
    if choice == "off":
        return {
            "type": operation_type,
            "mode": "off",
            "preset": None,
            "preset_version": None,
            "params": {},
            "risk": "low",
            "requires_preview": False,
            "approved": True,
        }
    if choice == "auto":
        return {
            "type": operation_type,
            "mode": "auto",
            "preset": None,
            "preset_version": None,
            "params": {},
            "risk": "medium",
            "requires_preview": True,
            "approved": False,
        }
    if choice == "custom":
        if not custom_params:
            raise InputError(f"{operation_type}=custom 需要对应的 JSON 参数", code="CUSTOM_PARAMS_REQUIRED")
        return {
            "type": operation_type,
            "mode": "custom",
            "preset": None,
            "preset_version": None,
            "params": custom_params,
            "risk": "medium",
            "requires_preview": True,
            "approved": False,
        }
    risk = "medium" if choice in medium_presets else "low"
    return {
        "type": operation_type,
        "mode": "preset",
        "preset": choice,
        "preset_version": "1.0",
        "params": copy.deepcopy(presets[choice]),
        "risk": risk,
        "requires_preview": risk == "medium",
        "approved": risk == "low",
    }


def _composition(mode: str, approved_high_risk: bool) -> dict[str, Any]:
    risk = "high" if mode == "stretch" else "medium" if mode in {"fill", "smart", "manual"} else "low"
    return {
        "type": "composition",
        "mode": mode,
        "preset": None,
        "preset_version": None,
        "params": {},
        "risk": risk,
        "requires_preview": risk != "low",
        "approved": approved_high_risk if risk == "high" else risk == "low",
    }


def _mirror_flip(mode: str, approved_high_risk: bool) -> dict[str, Any]:
    filters = {
        "off": [],
        "horizontal": ["hflip"],
        "vertical": ["vflip"],
        "both": ["hflip", "vflip"],
    }
    if mode not in filters:
        raise InputError(f"不支持的 mirror/flip mode：{mode}", code="INVALID_FLIP_MODE")
    risk = "low" if mode == "off" else "medium" if mode == "horizontal" else "high"
    return {
        "type": "mirror",
        "mode": mode,
        "preset": None,
        "preset_version": None,
        "params": {"ffmpeg_filters": filters[mode]},
        "risk": risk,
        "requires_preview": mode != "off",
        "approved": approved_high_risk if risk == "high" else mode == "off",
    }


def _parse_custom_json(raw: str | None, label: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(f"{label} 必须是 JSON object", code="INVALID_CUSTOM_PARAMS") from exc
    if not isinstance(value, dict):
        raise InputError(f"{label} 必须是 JSON object", code="INVALID_CUSTOM_PARAMS")
    return value


def build_operations(options: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    flip_mode = options.get("flip")
    if flip_mode is None:
        flip_mode = "horizontal" if options.get("mirror") else "off"
    operations: list[dict[str, Any]] = []
    trim_end = options.get("trim_end")
    if trim_end is not None:
        operations.append(
            {
                "type": "trim",
                "mode": "custom",
                "preset": None,
                "preset_version": None,
                "params": {
                    "start": 0.0,
                    "end": float(trim_end),
                    "evidence": list(options.get("trim_evidence") or ["user_specified"]),
                },
                "risk": "low",
                "requires_preview": False,
                "approved": True,
            }
        )
    speed = float(options["speed"])
    if not 0.25 <= speed <= 4.0:
        raise InputError("speed 必须在 0.25–4.0 之间", code="INVALID_SPEED")
    speed_risk = "low" if speed == 1.0 else "medium"
    operations.append(
        {
            "type": "speed",
            "mode": "off" if speed == 1.0 else "custom",
            "preset": None,
            "preset_version": None,
            "params": {"factor": speed},
            "risk": speed_risk,
            "requires_preview": speed_risk == "medium",
            "approved": speed_risk == "low",
        },
    )
    crop_aspect = options.get("crop_aspect", "off")
    if crop_aspect != "off":
        operations.append(
            {
                "type": "crop_reframe",
                "mode": "aspect",
                "preset": crop_aspect,
                "preset_version": "1.0",
                "params": {"aspect": crop_aspect, "anchor": options.get("crop_anchor", "top")},
                "risk": "medium",
                "requires_preview": True,
                "approved": False,
            }
        )
    safe_zoom = float(options.get("safe_zoom", 1.0))
    if not 1.0 <= safe_zoom <= 1.25:
        raise InputError("safe_zoom 必须在 1.0–1.25 之间", code="INVALID_SAFE_ZOOM")
    if safe_zoom > 1.0:
        zoom_risk = "high" if safe_zoom > 1.15 else "medium"
        operations.append(
            {
                "type": "safe_zoom",
                "mode": "custom",
                "preset": None,
                "preset_version": None,
                "params": {"factor": safe_zoom, "anchor": options.get("zoom_anchor", "top-left")},
                "risk": zoom_risk,
                "requires_preview": True,
                "approved": bool(options.get("approve_high_risk")) if zoom_risk == "high" else False,
            }
        )
    regions = list(options.get("removal_regions") or [])
    if regions:
        frame_area = max(1, int(options["input_width"]) * int(options["input_height"]))
        largest_ratio = max(region["width"] * region["height"] / frame_area for region in regions)
        removal_risk = "high" if len(regions) > 1 or largest_ratio > 0.08 else "medium"
        requested_backend = options.get("removal_backend", "auto")
        backend = "ffmpeg-delogo" if requested_backend == "auto" else requested_backend
        backend_note = (
            "auto 当前选择 ffmpeg-delogo；Gemini 后端仅适用于已验证的 Gemini 标准半透明模板"
            if requested_backend == "auto" else None
        )
        operations.append(
            {
                "type": "remove_region",
                "mode": "delogo",
                "preset": None,
                "preset_version": None,
                "params": {"regions": regions, "backend": backend, **({"routing_note": backend_note} if backend_note else {})},
                "risk": removal_risk,
                "requires_preview": True,
                "approved": options.get("approve_high_risk", False) if removal_risk == "high" else False,
            },
        )

    operations.extend(
        [
            _composition(options["composition"], options.get("approve_high_risk", False)),
            {
                "type": "geometry",
                "mode": "preserve" if options["resolution"] == "preserve" else "custom",
                "params": {"resolution": options["resolution"], "fps": options["fps"]},
                "risk": "low",
                "requires_preview": False,
                "approved": True,
            },
            _mirror_flip(flip_mode, options.get("approve_high_risk", False)),
            _selection(
                "denoise",
                options["denoise"],
                presets=DETAIL_PARAMS["denoise"],
                custom_params=_parse_custom_json(options.get("denoise_params"), "denoise params"),
                medium_presets={"medium"},
            ),
            _selection(
                "color",
                options["color"],
                presets=COLOR_PARAMS,
                custom_params=_parse_custom_json(options.get("color_params"), "color params"),
                medium_presets={"vivid"},
            ),
            _selection(
                "filter",
                options["filter"],
                presets=FILTER_PARAMS,
                custom_params=_parse_custom_json(options.get("filter_params"), "filter params"),
                medium_presets=set(FILTER_PARAMS),
            ),
            _selection(
                "sharpen",
                options["sharpen"],
                presets=DETAIL_PARAMS["sharpen"],
                custom_params=_parse_custom_json(options.get("sharpen_params"), "sharpen params"),
                medium_presets={"medium"},
            ),
            {
                "type": "quality",
                "mode": options.get("quality", "standard"),
                "preset": options.get("quality", "standard"),
                "preset_version": "1.0",
                "params": {
                    "scale_flags": "lanczos" if options.get("quality") in {"hd", "hd-plus"} else "bicubic",
                    "crf": 16 if options.get("quality") == "hd" else 14 if options.get("quality") == "hd-plus" else 18,
                    "encoder_preset": "slow" if options.get("quality") in {"hd", "hd-plus"} else "medium",
                },
                "risk": "medium" if options.get("quality") == "hd-plus" else "low",
                "requires_preview": options.get("quality") == "hd-plus",
                "approved": options.get("quality") != "hd-plus",
            },
        ]
    )
    if options.get("md5_rotate"):
        operations.append(
            {
                "type": "md5_rotate",
                "mode": "metadata",
                "preset": None,
                "preset_version": None,
                "params": {
                    "method": "container_metadata_tag",
                    "tag_prefix": "svmix-md5-rotate-v1",
                },
                "risk": "low",
                "requires_preview": False,
                "approved": True,
            }
        )

    conflicts = []
    color_enabled = next(item for item in operations if item["type"] == "color")["mode"] != "off"
    filter_enabled = next(item for item in operations if item["type"] == "filter")["mode"] != "off"
    if color_enabled and filter_enabled:
        conflicts.append(
            {
                "type": "color_filter_overlap",
                "status": "needs_review",
                "approved": bool(options.get("approve_conflicts")),
                "message": "调色与滤镜可能重复影响对比度、饱和度或色温。",
            }
        )
    else:
        conflicts.append(
            {
                "type": "color_filter_overlap",
                "status": "clear",
                "approved": True,
                "message": "未发现调色与滤镜叠加冲突。",
            }
        )
    return operations, conflicts


def build_plan(
    media: dict[str, Any],
    output_path: str | Path,
    options: dict[str, Any],
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = Path(media["path"]).resolve()
    destination = Path(output_path).expanduser().resolve()
    if source == destination:
        raise InputError("输出路径不能与输入相同", code="OUTPUT_CONFLICT")
    options = dict(options)
    options["resolution"] = parse_resolution(options["resolution"])
    options["fps"] = parse_fps(options["fps"])
    duration = media.get("duration")
    trim_end = options.get("trim_end")
    if trim_end is not None:
        if duration is None or not 0 < float(trim_end) < float(duration):
            raise InputError("trim_end 必须大于 0 且小于输入时长", code="INVALID_TRIM_END")
        options["trim_end"] = float(trim_end)
    if options["preset"] == "vertical-social" and (
        options["composition"] == "preserve" or options["resolution"] == "preserve"
    ):
        raise InputError(
            "vertical-social 必须显式指定 fit/fill/smart/stretch/manual 和目标 resolution",
            code="VERTICAL_PRESET_INCOMPLETE",
        )
    operations, conflicts = build_operations(options)
    preview_required = any(item["requires_preview"] for item in operations)
    stable_job_material = {
        "input_sha256": media["sha256"],
        "output": str(destination),
        "options": options,
    }
    job_id = hashlib.sha256(canonical_json(stable_job_material).encode("utf-8")).hexdigest()[:20]
    speed = float(options["speed"])
    base_duration = options.get("trim_end", duration)
    expected_duration = base_duration / speed if base_duration is not None else None
    plan = {
        "schema_version": SCHEMA_VERSION,
        "kind": "video-remix-plan",
        "job_id": job_id,
        "state": "planned",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": media,
        "analysis": analysis
        or {
            "profile": options.get("source", "generic"),
            "duplicate_group": None,
            "suggested_intervals": [],
            "uncertainties": ["未提供分析报告；计划未包含自动检测的废片段建议。"],
        },
        "preset": options["preset"],
        "operations": operations,
        "operation_order": [item["type"] for item in operations],
        "conflict_checks": conflicts,
        "preview": {
            "required": preview_required,
            "status": "approved" if preview_required and options.get("approve_preview") else "pending" if preview_required else "not_required",
            "path": None,
        },
        "encode": {
            "container": "mp4",
            "video_codec": "libx264",
            "crf": 16 if options.get("quality") == "hd" else 14 if options.get("quality") == "hd-plus" else 18,
            "preset": "slow" if options.get("quality") in {"hd", "hd-plus"} else "medium",
            "scale_flags": "lanczos" if options.get("quality") in {"hd", "hd-plus"} else "bicubic",
            "fps": options["fps"],
            "pixel_format": "yuv420p",
            "audio_codec": "aac" if media.get("has_audio") else None,
            "audio_bitrate": "192k" if media.get("has_audio") else None,
            "faststart": True,
        },
        "expected": {
            "duration": expected_duration,
            "duration_tolerance": max(0.12, 1.0 / 24.0),
            "resolution": options["resolution"],
            "fps": options["fps"],
            "has_audio": bool(media.get("has_audio")),
            "md5_rotated": bool(options.get("md5_rotate")),
        },
        "output": {"path": str(destination), "overwrite": False},
        "tool_versions": {
            "skill": SKILL_VERSION,
            "ffmpeg": tool_version("ffmpeg"),
            "ffprobe": tool_version("ffprobe"),
        },
    }
    if options.get("lineage"):
        plan["lineage"] = copy.deepcopy(options["lineage"])
    plan["plan_hash"] = compute_plan_hash(plan)
    return plan


def validate_plan_for_apply(plan: dict[str, Any]) -> None:
    verify_plan_hash(plan)
    input_data = plan.get("input") or {}
    source = input_data.get("path")
    if not source:
        raise PlanError("计划缺少 input.path", code="PLAN_INPUT_MISSING")
    actual_hash = sha256_file(source)
    if actual_hash != input_data.get("sha256"):
        raise PlanError("输入哈希已变化，拒绝执行旧计划", code="INPUT_HASH_MISMATCH")
    output_path = Path((plan.get("output") or {}).get("path", "")).expanduser().resolve()
    if output_path == Path(source).expanduser().resolve():
        raise PlanError("输出路径与输入相同", code="OUTPUT_CONFLICT")
    unapproved = [
        operation.get("type")
        for operation in plan.get("operations", [])
        if operation.get("risk") == "high" and not operation.get("approved")
    ]
    if unapproved:
        raise PlanError(
            f"存在未审批的 high-risk operation：{', '.join(unapproved)}",
            code="HIGH_RISK_NOT_APPROVED",
        )
    unresolved_auto = [
        operation.get("type") for operation in plan.get("operations", []) if operation.get("mode") == "auto"
    ]
    if unresolved_auto:
        raise PlanError(
            f"auto operation 尚未展开为固定参数：{', '.join(unresolved_auto)}",
            code="AUTO_NOT_RESOLVED",
        )
    preview = plan.get("preview") or {}
    if preview.get("required") and preview.get("status") != "approved":
        raise PlanError("计划要求预览，但 preview 尚未 approved", code="PREVIEW_NOT_APPROVED")
    unresolved_conflicts = [
        check.get("type")
        for check in plan.get("conflict_checks", [])
        if check.get("status") == "needs_review" and not check.get("approved")
    ]
    if unresolved_conflicts:
        raise PlanError(
            f"存在未审批的 conflict check：{', '.join(unresolved_conflicts)}",
            code="CONFLICT_NOT_APPROVED",
        )
