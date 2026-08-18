import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, SKILL_VERSION
from .analyzer import analyze_tail
from .constants import EXIT_OK, EXIT_VERIFY, SUPPORTED_EXTENSIONS
from .errors import InputError, VideoRemixError
from .executor import execute_plan
from .fingerprint import build_candidate_report
from .io_utils import read_json, write_json
from .md5rotate import rotate_md5
from .metadata import sanitize_metadata
from .media import md5_file, probe_media, sha256_file, tool_version
from .normalize import normalize_download
from .plans import (
    COLOR_CHOICES,
    COMPOSITION_MODES,
    DETAIL_CHOICES,
    FILTER_CHOICES,
    FLIP_CHOICES,
    build_plan,
    parse_region,
    verify_plan_hash,
)

SOURCE_PROFILES = ("generic", "douyin", "tiktok", "instagram", "youtube-short")
OUTPUT_PRESETS = ("preserve", "vertical-social", "preview-fast")


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="stdout 只输出 JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_pipeline.py",
        description="SuperVideoMix：多证据分析、精确去重、类型化计划、安全执行与验证。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {SKILL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="探测单个视频并生成只读分析报告")
    analyze.add_argument("input")
    analyze.add_argument("--source", choices=SOURCE_PROFILES, default="generic")
    analyze.add_argument("--no-tail-analysis", action="store_true", help="只做媒体探测，不扫描废片尾")
    analyze.add_argument("--report", required=True)
    _add_json_flag(analyze)
    analyze.set_defaults(handler=command_analyze)

    normalize = subparsers.add_parser(
        "normalize",
        help="下载后将 VP9/VP09 或其他不兼容编码转换为 QuickTime 兼容的 H.264/AAC MP4",
    )
    normalize.add_argument("input")
    normalize.add_argument("--output", required=True, help="新的 .mp4 输出路径，不得覆盖输入")
    normalize.add_argument("--report", required=True, help="兼容化报告 JSON 输出路径")
    _add_json_flag(normalize)
    normalize.set_defaults(handler=command_normalize)

    metadata = subparsers.add_parser("metadata", help="清理兼容 MP4 的下载器和隐私元数据，源文件不动")
    metadata.add_argument("input")
    metadata.add_argument("--output", required=True, help="新的 .mp4 输出路径")
    metadata.add_argument("--report", required=True)
    _add_json_flag(metadata)
    metadata.set_defaults(handler=command_metadata)

    md5_rotate = subparsers.add_parser(
        "md5-rotate",
        help="对成品 MP4 做容器元数据 remux，轮换文件 MD5/SHA-256，画面与声音不变",
    )
    md5_rotate.add_argument("input")
    md5_rotate.add_argument("--output", required=True, help="新的 .mp4 输出路径，不得覆盖输入")
    md5_rotate.add_argument("--tag", help="可选自定义元数据 tag；默认自动生成唯一 tag")
    md5_rotate.add_argument("--report", required=True)
    _add_json_flag(md5_rotate)
    md5_rotate.set_defaults(handler=command_md5_rotate)

    fingerprint = subparsers.add_parser("fingerprint", help="计算视频感知指纹并生成相似候选组")
    fingerprint.add_argument("inputs", nargs="+")
    fingerprint.add_argument("--threshold", type=float, default=0.86, help="候选相似度阈值，默认 0.86")
    fingerprint.add_argument("--report", required=True)
    _add_json_flag(fingerprint)
    fingerprint.set_defaults(handler=command_fingerprint)

    dedupe = subparsers.add_parser("dedupe", help="使用 SHA-256 查找完全相同素材，不删除文件")
    dedupe.add_argument("inputs", nargs="+")
    dedupe.add_argument("--report", required=True)
    _add_json_flag(dedupe)
    dedupe.set_defaults(handler=command_dedupe)

    plan = subparsers.add_parser("plan", help="生成稳定、可审查、可哈希的处理计划")
    plan.add_argument("input")
    plan.add_argument("--analysis")
    plan.add_argument("--source", choices=SOURCE_PROFILES, default="generic")
    plan.add_argument("--preset", choices=OUTPUT_PRESETS, default="preserve")
    plan.add_argument("--composition", choices=COMPOSITION_MODES, default="preserve")
    plan.add_argument("--resolution", default="preserve")
    plan.add_argument("--fps", default="preserve")
    plan.add_argument("--crop-aspect", choices=("off", "3:4"), default="off")
    plan.add_argument(
        "--crop-anchor",
        choices=("top", "center", "bottom", "left", "right"),
        default="top",
        help="目标画幅裁切锚点；9:16→3:4 去右下水印通常选 top",
    )
    plan.add_argument(
        "--safe-zoom",
        type=float,
        default=1.0,
        help="1.0–1.25 倍轻度放大；与 top-left 锚点组合可排除右下固定水印",
    )
    plan.add_argument(
        "--zoom-anchor",
        choices=("top-left", "top-right", "bottom-left", "bottom-right", "center"),
        default="top-left",
    )
    plan.add_argument("--quality", choices=("standard", "hd", "hd-plus"), default="standard")
    plan.add_argument(
        "--md5-rotate",
        action="store_true",
        help="成品交付前对输出做容器元数据 remux，轮换文件 MD5（画面/声音不变）",
    )
    mirror_group = plan.add_mutually_exclusive_group()
    mirror_group.add_argument(
        "--mirror",
        dest="flip",
        action="store_const",
        const="horizontal",
        help="水平镜像快捷参数，等价于 --flip horizontal",
    )
    mirror_group.add_argument(
        "--flip",
        dest="flip",
        choices=FLIP_CHOICES,
        help="镜像/反转方向；vertical 和 both 属于 high risk",
    )
    plan.add_argument("--denoise", choices=DETAIL_CHOICES, default="off")
    plan.add_argument("--denoise-params", help="custom 模式的 JSON object")
    plan.add_argument("--color", choices=COLOR_CHOICES, default="off")
    plan.add_argument("--color-params", help="custom 模式的 JSON object")
    plan.add_argument("--filter", choices=FILTER_CHOICES, default="off")
    plan.add_argument("--filter-params", help="custom 模式的 JSON object")
    plan.add_argument("--sharpen", choices=DETAIL_CHOICES, default="off")
    plan.add_argument("--sharpen-params", help="custom 模式的 JSON object")
    plan.add_argument("--speed", type=float, default=1.0)
    trim_group = plan.add_mutually_exclusive_group()
    trim_group.add_argument(
        "--accept-suggested-tail",
        action="store_true",
        help="接受 analysis 中的 junk_tail 建议并写入 trim operation",
    )
    trim_group.add_argument("--trim-end", type=float, help="显式保留 0 到该秒数，写入已批准 trim operation")
    plan.add_argument(
        "--remove-region",
        action="append",
        default=[],
        metavar="X:Y:W:H",
        help="获授权的固定清理区域；支持百分比，可重复",
    )
    plan.add_argument(
        "--removal-backend",
        choices=("auto", "ffmpeg-delogo", "gemini-watermark-remover", "video-inpaint"),
        default="auto",
        help="水印后端；auto 仅根据类型生成路由建议，不会盲目调用 Gemini",
    )
    plan.add_argument("--subtitle-band", metavar="START:END", help="获授权的底部硬字幕纵向比例，例如 0.72:0.94")
    plan.add_argument("--confirm-authorized-removal", action="store_true", help="确认有权移除指定区域")
    plan.add_argument("--approve-preview", action="store_true", help="确认已审查预览并批准中风险操作")
    plan.add_argument("--approve-conflicts", action="store_true", help="确认已审查并接受调色/滤镜重叠风险")
    plan.add_argument("--approve-high-risk", action="store_true")
    plan.add_argument("--final-output", help="成品绝对或相对路径；默认 INPUT.remix.mp4")
    plan.add_argument("--source-key", help="统一资产账本 source_key，例如 instagram:DaBCJx9CdIU")
    plan.add_argument("--source-creator", help="规范来源博主，仅用于展示和审计")
    plan.add_argument("--parent-asset-id", help="输入文件在统一账本中的 asset_id")
    plan.add_argument("--batch-id", help="本次 Remix/拆分批次 ID")
    plan.add_argument("--recipe-id", help="稳定处理配方 ID")
    plan.add_argument("--agent", default="super-video-mix", help="执行 Agent 标识")
    plan.add_argument("--output", required=True, help="plan JSON 输出路径")
    _add_json_flag(plan)
    plan.set_defaults(handler=command_plan, flip="off")

    apply = subparsers.add_parser("apply", help="安全执行当前已支持的计划操作")
    apply.add_argument("plan")
    apply.add_argument("--report", help="execution JSON；默认与 plan 同目录")
    apply.add_argument("--catalog-root", help="Video_Download 根目录；提供后自动导入 verified 回执")
    apply.add_argument("--catalog-cli", help="media_asset_catalog.py 路径；默认自动查找 superdown88")
    _add_json_flag(apply)
    apply.set_defaults(handler=command_apply)

    verify = subparsers.add_parser("verify", help="验证计划和已有输出")
    verify.add_argument("plan")
    verify.add_argument("--report", required=True)
    _add_json_flag(verify)
    verify.set_defaults(handler=command_verify)
    return parser


def _emit(payload: dict[str, Any], *, json_mode: bool, summary: str) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(summary, file=sys.stderr)


def _tool_versions() -> dict[str, str]:
    return {
        "skill": SKILL_VERSION,
        "ffmpeg": tool_version("ffmpeg"),
        "ffprobe": tool_version("ffprobe"),
    }


def command_analyze(args: argparse.Namespace) -> int:
    media = probe_media(args.input)
    tail = None if args.no_tail_analysis else analyze_tail(args.input, media, args.source)
    suggested_intervals = [] if tail is None else tail["suggested_intervals"]
    uncertainties = []
    if tail is None:
        uncertainties.append("已通过 --no-tail-analysis 关闭废片尾扫描。")
    elif suggested_intervals:
        uncertainties.append("废片尾区间是多证据建议，必须复核后才能写入执行计划。")
    else:
        uncertainties.append("尾部扫描未发现达到当前阈值的连续废片段。")
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": "video-analysis",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "analyzed",
        "input": media,
        "profile": args.source,
        "findings": {
            "duplicate_group": None,
            "suggested_intervals": suggested_intervals,
            "tail_analysis": tail,
            "uncertainties": uncertainties,
        },
        "tool_versions": _tool_versions(),
    }
    report_path = write_json(args.report, report)
    result = {"status": "ok", "report": str(report_path), "analysis": report}
    _emit(result, json_mode=args.json, summary=f"分析完成：{report_path}")
    return EXIT_OK


def command_normalize(args: argparse.Namespace) -> int:
    report = normalize_download(args.input, args.output)
    report_path = write_json(args.report, report)
    result = {
        "status": report["status"],
        "report": str(report_path),
        "output": report["output_path"],
        "verified": report["verified"],
        "reasons": report["reasons"],
    }
    _emit(result, json_mode=args.json, summary=f"编码兼容化完成：{report['output_path']}")
    return EXIT_OK


def command_metadata(args: argparse.Namespace) -> int:
    report = sanitize_metadata(args.input, args.output)
    report_path = write_json(args.report, report)
    result = {"status": report["status"], "report": str(report_path), "output": report["output_path"], "verified": True}
    _emit(result, json_mode=args.json, summary=f"元数据规范化完成：{report['output_path']}")
    return EXIT_OK


def command_md5_rotate(args: argparse.Namespace) -> int:
    report = rotate_md5(args.input, args.output, tag=args.tag)
    report_path = write_json(args.report, report)
    result = {
        "status": report["status"],
        "report": str(report_path),
        "output": report["output"]["path"],
        "input_md5": report["input"]["md5"],
        "output_md5": report["output"]["md5"],
        "verified": report["verified"],
    }
    _emit(result, json_mode=args.json, summary=f"MD5 轮换完成：{report['output']['path']}")
    return EXIT_OK


def command_fingerprint(args: argparse.Namespace) -> int:
    if not 0.0 < args.threshold <= 1.0:
        raise InputError("threshold 必须在 0 到 1 之间", code="FINGERPRINT_THRESHOLD_INVALID")
    files = _discover_files(args.inputs)
    report = build_candidate_report(files, args.threshold)
    report_path = write_json(args.report, report)
    result = {
        "status": "ok",
        "report": str(report_path),
        "files_scanned": report["files_scanned"],
        "candidate_group_count": len(report["candidate_groups"]),
        "similar_candidate_count": len(report["similar_candidates"]),
    }
    _emit(result, json_mode=args.json, summary=f"感知指纹完成：发现 {len(report['candidate_groups'])} 个候选组；报告 {report_path}")
    return EXIT_OK


def _discover_files(values: list[str]) -> list[Path]:
    discovered: set[Path] = set()
    for raw in values:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise InputError(f"输入不存在：{path}", code="INPUT_NOT_FOUND")
        if path.is_file():
            discovered.add(path)
            continue
        for candidate in path.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
                discovered.add(candidate.resolve())
    if not discovered:
        raise InputError("没有发现支持的视频文件", code="NO_MEDIA_FOUND")
    return sorted(discovered, key=lambda item: str(item))


def command_dedupe(args: argparse.Namespace) -> int:
    files = _discover_files(args.inputs)
    by_hash: dict[str, list[dict[str, Any]]] = {}
    for path in files:
        digest = sha256_file(path)
        by_hash.setdefault(digest, []).append(
            {"path": str(path), "size": path.stat().st_size, "sha256": digest}
        )
    groups = [
        {"classification": "exact", "sha256": digest, "items": items}
        for digest, items in sorted(by_hash.items())
        if len(items) > 1
    ]
    unique = [items[0] for items in by_hash.values() if len(items) == 1]
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": "exact-deduplication",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": "sha256",
        "files_scanned": len(files),
        "exact_groups": groups,
        "unique": sorted(unique, key=lambda item: item["path"]),
        "deleted": [],
        "uncertainties": ["当前版本尚未运行跨时间感知指纹或音频相似度。"],
    }
    report_path = write_json(args.report, report)
    result = {"status": "ok", "report": str(report_path), "exact_group_count": len(groups)}
    _emit(result, json_mode=args.json, summary=f"精确去重完成：发现 {len(groups)} 个重复组；报告 {report_path}")
    return EXIT_OK


def _analysis_snapshot(path: str | None, media: dict[str, Any]) -> dict[str, Any] | None:
    if not path:
        return None
    report = read_json(path)
    analysis_input = report.get("input") or {}
    if analysis_input.get("sha256") != media.get("sha256"):
        raise InputError("analysis 与当前输入哈希不一致", code="ANALYSIS_INPUT_MISMATCH")
    return {
        "profile": report.get("profile", "generic"),
        "duplicate_group": (report.get("findings") or {}).get("duplicate_group"),
        "suggested_intervals": (report.get("findings") or {}).get("suggested_intervals", []),
        "uncertainties": (report.get("findings") or {}).get("uncertainties", []),
        "report_path": str(Path(path).expanduser().resolve()),
    }


def command_plan(args: argparse.Namespace) -> int:
    media = probe_media(args.input)
    analysis = _analysis_snapshot(args.analysis, media)
    source = Path(media["path"])
    final_output = (
        Path(args.final_output).expanduser().resolve()
        if args.final_output
        else source.with_name(f"{source.stem}.remix.mp4")
    )
    trim_end = args.trim_end
    trim_evidence = ["user_specified"] if trim_end is not None else []
    if args.accept_suggested_tail:
        if analysis is None:
            raise InputError("--accept-suggested-tail 需要 --analysis", code="ANALYSIS_REQUIRED")
        suggestion = next(
            (item for item in analysis.get("suggested_intervals", []) if item.get("type") == "junk_tail"),
            None,
        )
        if suggestion is None:
            raise InputError("analysis 中没有 junk_tail 建议", code="TAIL_SUGGESTION_MISSING")
        trim_end = float(suggestion["start"])
        trim_evidence = list(suggestion.get("evidence") or [])
    width = int(media["width"])
    height = int(media["height"])
    removal_regions = [parse_region(value, width, height) for value in args.remove_region]
    if args.subtitle_band:
        try:
            start_raw, end_raw = args.subtitle_band.split(":", 1)
            start_fraction, end_fraction = float(start_raw), float(end_raw)
        except ValueError as exc:
            raise InputError("subtitle-band 必须是 START:END", code="INVALID_SUBTITLE_BAND") from exc
        if not 0 <= start_fraction < end_fraction <= 1:
            raise InputError("subtitle-band 必须满足 0 <= START < END <= 1", code="INVALID_SUBTITLE_BAND")
        y = round(height * start_fraction)
        removal_regions.append(
            {"x": 1, "y": y, "width": width - 2, "height": max(3, round(height * end_fraction) - y)}
        )
    if removal_regions and not args.confirm_authorized_removal:
        raise InputError(
            "指定清理区域前必须加入 --confirm-authorized-removal",
            code="REMOVAL_AUTHORIZATION_REQUIRED",
        )
    options = {
        "source": args.source,
        "preset": args.preset,
        "composition": args.composition,
        "resolution": args.resolution,
        "fps": args.fps,
        "crop_aspect": args.crop_aspect,
        "crop_anchor": args.crop_anchor,
        "safe_zoom": args.safe_zoom,
        "zoom_anchor": args.zoom_anchor,
        "quality": args.quality,
        "md5_rotate": args.md5_rotate,
        "flip": args.flip,
        "denoise": args.denoise,
        "denoise_params": args.denoise_params,
        "color": args.color,
        "color_params": args.color_params,
        "filter": args.filter,
        "filter_params": args.filter_params,
        "sharpen": args.sharpen,
        "sharpen_params": args.sharpen_params,
        "speed": args.speed,
        "trim_end": trim_end,
        "trim_evidence": trim_evidence,
        "removal_regions": removal_regions,
        "removal_backend": args.removal_backend,
        "input_width": width,
        "input_height": height,
        "approve_preview": args.approve_preview,
        "approve_conflicts": args.approve_conflicts,
        "approve_high_risk": args.approve_high_risk,
        "lineage": {
            "schema": "supermedia.lineage/v1",
            "source_key": args.source_key,
            "source_creator": args.source_creator,
            "parent_asset_id": args.parent_asset_id,
            "batch_id": args.batch_id,
            "recipe_id": args.recipe_id,
            "operation": "video-transform",
            "agent": args.agent,
        }
        if args.source_key
        else None,
    }
    plan = build_plan(media, final_output, options, analysis)
    plan_path = write_json(args.output, plan)
    needs_review = [
        item
        for item in plan["conflict_checks"]
        if item["status"] == "needs_review" and not item.get("approved")
    ]
    preview_pending = plan["preview"]["required"] and plan["preview"]["status"] != "approved"
    result = {
        "status": "needs_review" if needs_review or preview_pending else "ok",
        "plan": str(plan_path),
        "plan_hash": plan["plan_hash"],
        "preview_required": plan["preview"]["required"],
        "preview_status": plan["preview"]["status"],
        "conflict_checks": plan["conflict_checks"],
    }
    _emit(result, json_mode=args.json, summary=f"计划已生成：{plan_path}；状态 {result['status']}")
    return EXIT_OK


def command_apply(args: argparse.Namespace) -> int:
    plan = read_json(args.plan)
    execution = execute_plan(plan)
    report_path = (
        Path(args.report).expanduser().resolve()
        if args.report
        else Path(args.plan).expanduser().resolve().with_suffix(".execution.json")
    )
    write_json(report_path, execution)
    catalog_result = None
    if args.catalog_root:
        lineage = execution.get("lineage")
        if not lineage or not lineage.get("source_key"):
            raise InputError(
                "--catalog-root 要求 plan 由 --source-key 创建",
                code="CATALOG_LINEAGE_REQUIRED",
            )
        catalog_cli = _resolve_catalog_cli(args.catalog_cli)
        process = subprocess.run(
            [
                sys.executable,
                str(catalog_cli),
                "--root",
                str(Path(args.catalog_root).expanduser().resolve()),
                "ingest-receipt",
                str(report_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode:
            raise InputError(
                f"成品已验证，但统一账本导入失败：{process.stderr.strip() or process.stdout.strip()}",
                code="CATALOG_INGEST_FAILED",
            )
        catalog_result = json.loads(process.stdout)
    result = {
        "status": "verified",
        "report": str(report_path),
        "output": execution["output"]["path"],
        "plan_hash": execution["plan_hash"],
        "catalog": catalog_result,
    }
    _emit(result, json_mode=args.json, summary=f"执行并验证完成：{execution['output']['path']}")
    return EXIT_OK


def _resolve_catalog_cli(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("SUPER_MEDIA_CATALOG_CLI"):
        candidates.append(Path(os.environ["SUPER_MEDIA_CATALOG_CLI"]))
    candidates.extend(
        [
            Path.home() / ".codex/skills/superdown88/scripts/media_asset_catalog.py",
            Path.home() / ".agents/skills/superdown88/scripts/media_asset_catalog.py",
        ]
    )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file():
            return resolved
    raise InputError(
        "未找到 media_asset_catalog.py；请传 --catalog-cli 或设置 SUPER_MEDIA_CATALOG_CLI",
        code="CATALOG_CLI_MISSING",
    )


def _resolution_matches(expected: str, media: dict[str, Any], input_media: dict[str, Any]) -> bool:
    if expected == "preserve":
        return media.get("width") == input_media.get("width") and media.get("height") == input_media.get("height")
    width, height = (int(item) for item in expected.split("x", 1))
    return media.get("width") == width and media.get("height") == height


def command_verify(args: argparse.Namespace) -> int:
    plan = read_json(args.plan)
    checks = []
    try:
        verify_plan_hash(plan)
        checks.append({"name": "plan_hash", "status": "pass"})
    except VideoRemixError as exc:
        checks.append({"name": "plan_hash", "status": "fail", "message": str(exc)})

    output = Path((plan.get("output") or {}).get("path", "")).expanduser().resolve()
    output_media = None
    if not output.exists():
        checks.append({"name": "output_exists", "status": "fail", "message": f"输出不存在：{output}"})
    else:
        checks.append({"name": "output_exists", "status": "pass"})
        try:
            output_media = probe_media(output)
            checks.append({"name": "decodable_video", "status": "pass"})
        except VideoRemixError as exc:
            checks.append({"name": "decodable_video", "status": "fail", "message": str(exc)})

    if output_media:
        expected = plan.get("expected") or {}
        resolution_ok = _resolution_matches(
            expected.get("resolution", "preserve"),
            output_media,
            plan.get("input") or {},
        )
        checks.append({"name": "resolution", "status": "pass" if resolution_ok else "fail"})
        expected_audio = expected.get("has_audio")
        audio_ok = expected_audio is None or bool(output_media.get("has_audio")) == bool(expected_audio)
        checks.append({"name": "audio_stream", "status": "pass" if audio_ok else "fail"})
        duration = expected.get("duration")
        actual_duration = output_media.get("duration")
        if duration is not None and actual_duration is not None:
            tolerance = expected.get("duration_tolerance", 0.05)
            duration_ok = abs(actual_duration - duration) <= tolerance
            checks.append(
                {
                    "name": "duration",
                    "status": "pass" if duration_ok else "fail",
                    "expected": duration,
                    "actual": actual_duration,
                    "tolerance": tolerance,
                }
            )
    if (plan.get("expected") or {}).get("md5_rotated") and output.exists():
        input_md5 = md5_file(Path((plan.get("input") or {}).get("path", "")).expanduser().resolve())
        output_md5 = md5_file(output)
        checks.append(
            {
                "name": "md5_rotated",
                "status": "pass" if input_md5 != output_md5 else "fail",
                "input_md5": input_md5,
                "output_md5": output_md5,
            }
        )

    verified = bool(checks) and all(item["status"] == "pass" for item in checks)
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": "video-verification",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verified": verified,
        "plan": str(Path(args.plan).expanduser().resolve()),
        "output": str(output),
        "checks": checks,
        "output_media": output_media,
    }
    report_path = write_json(args.report, report)
    result = {"status": "verified" if verified else "failed", "report": str(report_path), "checks": checks}
    _emit(result, json_mode=args.json, summary=f"验证{'通过' if verified else '失败'}：{report_path}")
    return EXIT_OK if verified else EXIT_VERIFY


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except VideoRemixError as exc:
        payload = {"status": "error", "error": {"code": exc.code, "message": str(exc)}}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"错误 [{exc.code}]：{exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("错误 [INTERRUPTED]：任务被中断", file=sys.stderr)
        return 130
