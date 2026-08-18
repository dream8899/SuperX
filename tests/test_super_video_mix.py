import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from videoremix.errors import PlanError  # noqa: E402
from videoremix.analyzer import FrameMetric, Profile, find_tail_cut  # noqa: E402
from videoremix.media import probe_media, sha256_file  # noqa: E402
from videoremix.plans import (  # noqa: E402
    build_plan,
    compute_plan_hash,
    parse_region,
    validate_plan_for_apply,
    verify_plan_hash,
)
from content_split import find_false_split_candidates, find_peaks, split_one  # noqa: E402
from permutation_concat import run as permutation_run  # noqa: E402
from repair_false_splits import rebuild_segments  # noqa: E402
from smart_label_detect import analyze as analyze_labels  # noqa: E402
from smart_label_detect import calculate_crop, detect_spatial_candidates, match_target_label  # noqa: E402
from free_crop_remix import crop_rect  # noqa: E402


def base_options(**overrides):
    value = {
        "source": "generic",
        "preset": "preserve",
        "composition": "preserve",
        "resolution": "preserve",
        "fps": "preserve",
        "flip": "off",
        "denoise": "off",
        "denoise_params": None,
        "color": "off",
        "color_params": None,
        "filter": "off",
        "filter_params": None,
        "sharpen": "off",
        "sharpen_params": None,
        "speed": 1.0,
        "approve_high_risk": False,
    }
    value.update(overrides)
    return value


class VideoRemixPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "input.mp4"
        self.source.write_bytes(b"phase-1-media-fixture")
        self.media = {
            "path": str(self.source.resolve()),
            "size": self.source.stat().st_size,
            "sha256": sha256_file(self.source),
            "duration": 10.0,
            "width": 1920,
            "height": 1080,
            "fps": "30/1",
            "has_audio": True,
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_plan(self, **overrides):
        with mock.patch("videoremix.plans.tool_version", return_value="test-tool 1.0"):
            return build_plan(
                self.media,
                self.root / "output.mp4",
                base_options(**overrides),
            )

    def test_preserve_plan_keeps_all_optional_enhancements_off(self):
        plan = self.make_plan()
        by_type = {item["type"]: item for item in plan["operations"]}
        self.assertEqual(by_type["composition"]["mode"], "preserve")
        for operation_type in ("speed", "mirror", "denoise", "color", "filter", "sharpen"):
            self.assertEqual(by_type[operation_type]["mode"], "off")
        self.assertFalse(plan["preview"]["required"])
        verify_plan_hash(plan)

    def test_plan_preserves_versioned_lineage_in_plan_hash(self):
        lineage = {
            "schema": "supermedia.lineage/v1",
            "source_key": "instagram:ABC123",
            "source_creator": "maker",
            "parent_asset_id": "asset_source",
            "batch_id": "batch-1",
            "recipe_id": "preserve-v1",
            "operation": "video-transform",
            "agent": "test-agent",
        }
        plan = self.make_plan(lineage=lineage)
        self.assertEqual(plan["lineage"], lineage)
        verify_plan_hash(plan)

    def test_enhancements_are_independent_and_conflicts_are_reported(self):
        plan = self.make_plan(
            composition="fit",
            resolution="1080x1920",
            color="natural",
            filter="cinematic",
            denoise="light",
            sharpen="medium",
            speed=1.06,
        )
        by_type = {item["type"]: item for item in plan["operations"]}
        self.assertEqual(by_type["color"]["preset"], "natural")
        self.assertEqual(by_type["filter"]["preset"], "cinematic")
        self.assertEqual(by_type["speed"]["params"]["factor"], 1.06)
        self.assertTrue(plan["preview"]["required"])
        self.assertEqual(plan["conflict_checks"][0]["status"], "needs_review")

    def test_content_split_clusters_nearby_peaks_without_duplicate_tail(self):
        peaks = find_peaks([(1.0, 0.3), (1.4, 0.5), (5.0, 0.45)], min_distance=1.5)
        self.assertEqual([(round(t, 1), round(score, 1)) for t, score, _ in peaks], [(1.4, 0.5), (5.0, 0.5)])

    def test_short_local_segment_is_routed_to_false_split_review(self):
        segments = [
            {"start": 0.0, "end": 8.1, "duration": 8.1},
            {"start": 8.1, "end": 18.1, "duration": 10.0},
            {"start": 18.1, "end": 22.2, "duration": 4.1},
            {"start": 22.2, "end": 26.1, "duration": 3.9},
        ]
        candidates = find_false_split_candidates(segments)
        self.assertEqual([item["boundary_index"] for item in candidates], [2, 3])
        self.assertEqual(candidates[-1]["time"], 22.2)

    def test_review_required_analysis_cannot_execute_without_approval(self):
        analysis = {
            "status": "needs_review",
            "segments": [
                {"start": 0.0, "end": 5.0, "duration": 5.0},
                {"start": 5.0, "end": 10.0, "duration": 5.0},
            ],
            "media": {"has_audio": False},
            "usable_duration": 10.0,
            "parameters": {},
        }
        result = split_one(self.source, analysis, self.root / "out")
        self.assertEqual(result["status"], "skipped")
        self.assertIn("review_required", result["reason"])

    def test_repair_plan_merges_multiple_adjacent_false_boundaries(self):
        segments = [
            {"start": 0.0, "end": 10.1, "duration": 10.1},
            {"start": 10.1, "end": 13.1, "duration": 3.0},
            {"start": 13.1, "end": 15.9, "duration": 2.8},
            {"start": 15.9, "end": 17.9, "duration": 2.0},
            {"start": 17.9, "end": 20.1, "duration": 2.2},
            {"start": 20.1, "end": 30.1, "duration": 10.0},
        ]
        rebuilt = rebuild_segments(segments, [2, 3, 4])
        self.assertEqual([(item["start"], item["end"]) for item in rebuilt],
                         [(0.0, 10.1), (10.1, 20.1), (20.1, 30.1)])

    def test_spatial_overlay_detection_prefers_minimum_loss_edge_crop(self):
        width, height = 64, 96
        frames = []
        for frame_index in range(8):
            pixels = bytearray(width * height)
            for y in range(height):
                for x in range(width):
                    pixels[y * width + x] = (x * 2 + y + frame_index * 11) % 180
            for y in range(80, 96):
                for x in range(48, 64):
                    pixels[y * width + x] = 245 if (x + y) % 2 else 15
            frames.append(bytes(pixels))
        candidates = detect_spatial_candidates(frames, width, height)
        corner = next(item for item in candidates if {"right", "bottom"}.issubset(item["touches_edges"]))
        detection = {"text_position": "none", "top_text_band": None, "bot_text_band": None,
                     "logo_band": None, "overlay_candidates": [corner]}
        crop = calculate_crop(detection, width, height)
        self.assertGreater(crop["bot_crop_px"], 0)
        self.assertEqual(crop["right_crop_px"], 0)
        self.assertTrue(crop["review_required"])

    def test_creator_memory_requires_explicit_confirmation(self):
        memory_dir = self.root / "memory"
        detection = {
            "text_position": "none", "top_text_band": None, "bot_text_band": None,
            "logo_band": None, "overlay_candidates": [], "resolution": "64x96",
        }
        with mock.patch("smart_label_detect.check_playability", return_value={"status": "verified"}), \
             mock.patch("smart_label_detect.extract_frames",
                        return_value=([bytes(64 * 96), bytes(64 * 96)], 64, 96, 5.0, 64, 96)), \
             mock.patch("smart_label_detect.detect_overlays", return_value=detection.copy()):
            analyze_labels(self.source, "creator", memory_dir, confirm_memory=False)
            self.assertFalse((memory_dir / "creator.json").exists())
            analyze_labels(self.source, "creator", memory_dir, confirm_memory=True)
            self.assertTrue((memory_dir / "creator.json").exists())

    def test_target_label_matching_is_stable_and_normalized(self):
        with mock.patch("smart_label_detect._ocr_pgm", return_value="MagicBox Studio"):
            result = match_target_label([bytes(64 * 32)] * 4, 64, 32, [0, 0, 64, 16], "magicbox.studio")
        self.assertGreaterEqual(result["target_match_score"], 0.9)
        self.assertEqual(result["target_match_rate"], 1.0)

    def test_free_crop_ignores_unaccepted_diagnostic_rectangle(self):
        item = {"crop": {"crop_rect": {"x": 0, "y": 200, "width": 1080, "height": 1600},
                          "accepted_candidates": []}}
        self.assertEqual(crop_rect(item, 1080, 1920),
                         (0, 0, 1080, 1920, "no_reliable_target_crop"))

    def test_mirror_and_flip_modes_have_explicit_ffmpeg_filters(self):
        horizontal = self.make_plan(flip="horizontal")
        horizontal_op = next(item for item in horizontal["operations"] if item["type"] == "mirror")
        self.assertEqual(horizontal_op["params"]["ffmpeg_filters"], ["hflip"])
        self.assertEqual(horizontal_op["risk"], "medium")
        self.assertTrue(horizontal["preview"]["required"])

        vertical = self.make_plan(flip="vertical")
        vertical_op = next(item for item in vertical["operations"] if item["type"] == "mirror")
        self.assertEqual(vertical_op["params"]["ffmpeg_filters"], ["vflip"])
        self.assertEqual(vertical_op["risk"], "high")
        with self.assertRaises(PlanError) as raised:
            validate_plan_for_apply(vertical)
        self.assertEqual(raised.exception.code, "HIGH_RISK_NOT_APPROVED")

    def test_unapproved_stretch_is_blocked_before_apply(self):
        plan = self.make_plan(composition="stretch", resolution="1080x1920")
        with self.assertRaises(PlanError) as raised:
            validate_plan_for_apply(plan)
        self.assertEqual(raised.exception.code, "HIGH_RISK_NOT_APPROVED")

        composition = next(item for item in plan["operations"] if item["type"] == "composition")
        composition["approved"] = True
        plan["preview"]["status"] = "approved"
        plan["plan_hash"] = compute_plan_hash(plan)
        validate_plan_for_apply(plan)

    def test_auto_operation_is_blocked_before_apply(self):
        plan = self.make_plan(color="auto")
        plan["preview"]["status"] = "approved"
        plan["plan_hash"] = compute_plan_hash(plan)
        with self.assertRaises(PlanError) as raised:
            validate_plan_for_apply(plan)
        self.assertEqual(raised.exception.code, "AUTO_NOT_RESOLVED")

    def test_plan_hash_detects_tampering(self):
        plan = self.make_plan()
        plan["encode"]["crf"] = 28
        with self.assertRaises(PlanError) as raised:
            verify_plan_hash(plan)
        self.assertEqual(raised.exception.code, "PLAN_HASH_MISMATCH")

    def test_plan_schema_is_valid_json(self):
        schema = json.loads((PROJECT_DIR / "references" / "mix" / "plan.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")

    def test_regions_support_pixels_and_percentages(self):
        self.assertEqual(
            parse_region("80%:5%:15%:10%", 1000, 2000),
            {"x": 800, "y": 100, "width": 150, "height": 200},
        )
        with self.assertRaises(Exception):
            parse_region("900:0:200:50", 1000, 1000)

    def test_manual_trim_changes_expected_duration(self):
        plan = self.make_plan(trim_end=7.5, trim_evidence=["user_specified"])
        trim = next(item for item in plan["operations"] if item["type"] == "trim")
        self.assertEqual(trim["params"]["end"], 7.5)
        self.assertEqual(plan["expected"]["duration"], 7.5)

    def test_md5_rotate_option_adds_typed_low_risk_operation(self):
        plan = self.make_plan(md5_rotate=True)
        operation = next(item for item in plan["operations"] if item["type"] == "md5_rotate")
        self.assertEqual(operation["mode"], "metadata")
        self.assertEqual(operation["params"]["method"], "container_metadata_tag")
        self.assertEqual(operation["risk"], "low")
        self.assertFalse(operation["requires_preview"])
        self.assertTrue(operation["approved"])
        self.assertTrue(plan["expected"]["md5_rotated"])
        verify_plan_hash(plan)


class TailAnalysisLogicTests(unittest.TestCase):
    def test_finds_contiguous_dark_silent_tail(self):
        profile = Profile(0.2, 10, 1.0, 4)
        metrics = [FrameMetric(index / 4, 100, 40, 30, 20, -20) for index in range(12)]
        metrics.extend(FrameMetric(index / 4, 2, 1, 1, 0, -60) for index in range(12, 20))
        cut_at, reasons = find_tail_cut(metrics, 5.0, profile)
        self.assertEqual(cut_at, 3.0)
        self.assertIn("dark", reasons)
        self.assertIn("silent", reasons)

    def test_ignores_tail_shorter_than_profile_minimum(self):
        profile = Profile(0.2, 10, 1.0, 4)
        metrics = [FrameMetric(index / 4, 100, 40, 30, 20, -20) for index in range(18)]
        metrics.extend(FrameMetric(index / 4, 2, 1, 1, 0, -60) for index in range(18, 20))
        cut_at, _ = find_tail_cut(metrics, 5.0, profile)
        self.assertIsNone(cut_at)


class PermutationConcatTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.input_dir = self.root / "segs"
        self.input_dir.mkdir()
        for stem in ("01_alpha", "02_beta", "03_gamma"):
            (self.input_dir / f"{stem}.mp4").write_bytes(b"fixture")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_first_position_dedup_and_01_exclusion_with_lossless_concat(self):
        output_dir = self.root / "out"
        calls = []

        def fake_run(command, **kwargs):
            first_line = None
            if "-f" in command:
                list_path = Path(command[command.index("-i") + 1])
                first_line = list_path.read_text(encoding="utf-8").splitlines()[0]
            calls.append((command, first_line))
            return subprocess.CompletedProcess(command, 0)

        with mock.patch("permutation_concat.subprocess.run", side_effect=fake_run):
            permutation_run(self.input_dir, output_dir)

        concat_calls = [(command, first_line) for command, first_line in calls if first_line is not None]
        self.assertEqual(len(concat_calls), 4)  # 2 段 x2 + 3 段 x2
        first_positions = []
        for command, first_line in concat_calls:
            self.assertEqual(command[command.index("-f") + 1], "concat")
            self.assertEqual(command[command.index("-c") + 1], "copy")
            first_positions.append(Path(first_line.split("'", 2)[1]).name)
        self.assertTrue(all(not name.startswith("01_") for name in first_positions))
        self.assertEqual(
            sorted(name[:2] for name in first_positions),
            ["02", "02", "03", "03"],
        )
        outputs = sorted({Path(command[-1]).resolve() for command, _ in concat_calls})
        self.assertEqual(len(outputs), 4)
        self.assertTrue(all(item.suffix == ".mp4" for item in outputs))
        self.assertTrue(all(output_dir.resolve() in item.parents for item in outputs))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
class VideoRemixCliTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "sample.mp4"
        process = subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=320x240:r=24:d=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=1",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-t",
                "1",
                str(self.source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            self.fail(process.stderr)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "video_pipeline.py"), *map(str, arguments)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_analyze_dedupe_plan_and_missing_output_verification(self):
        analysis = self.root / "analysis.json"
        result = self.run_cli("analyze", self.source, "--source", "douyin", "--report", analysis, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")
        self.assertTrue(analysis.exists())

        duplicate = self.root / "sample-copy.mp4"
        shutil.copyfile(self.source, duplicate)
        duplicates = self.root / "duplicates.json"
        result = self.run_cli("dedupe", self.root, "--report", duplicates, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["exact_group_count"], 1)

        plan = self.root / "plan.json"
        result = self.run_cli(
            "plan",
            self.source,
            "--analysis",
            analysis,
            "--color",
            "natural",
            "--filter",
            "cinematic",
            "--mirror",
            "--output",
            plan,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "needs_review")
        saved_plan = json.loads(plan.read_text(encoding="utf-8"))
        mirror = next(item for item in saved_plan["operations"] if item["type"] == "mirror")
        self.assertEqual(mirror["mode"], "horizontal")
        self.assertEqual(mirror["params"]["ffmpeg_filters"], ["hflip"])

        verification = self.root / "verification.json"
        result = self.run_cli("verify", plan, "--report", verification, "--json")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")

    def test_normalize_vp9_download_to_h264_aac_mp4(self):
        vp9_source = self.root / "download-vp9.mp4"
        encode = subprocess.run(
            [
                shutil.which("ffmpeg"), "-v", "error", "-i", str(self.source),
                "-c:v", "libvpx-vp9", "-crf", "35", "-b:v", "0",
                "-c:a", "aac", "-b:a", "96k", "-y", str(vp9_source),
            ],
            capture_output=True, text=True, check=False,
        )
        if encode.returncode != 0:
            self.skipTest(f"ffmpeg 不支持 libvpx-vp9：{encode.stderr}")
        self.assertEqual(probe_media(vp9_source)["video_codec"], "vp9")
        output = self.root / "download-h264-aac.mp4"
        report = self.root / "normalize.json"
        result = self.run_cli(
            "normalize", vp9_source, "--output", output, "--report", report, "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "converted")
        self.assertTrue(payload["verified"])
        normalized = probe_media(output)
        self.assertEqual(normalized["video_codec"], "h264")
        self.assertEqual(normalized["audio_codec"], "aac")
        self.assertNotEqual(vp9_source, output)

    def test_metadata_sanitize_and_perceptual_candidate_group(self):
        sanitized = self.root / "sample-sanitized.mp4"
        metadata_report = self.root / "metadata.json"
        result = self.run_cli(
            "metadata", self.source, "--output", sanitized, "--report", metadata_report, "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata_payload = json.loads(result.stdout)
        self.assertEqual(metadata_payload["status"], "metadata_sanitized")
        self.assertTrue(metadata_payload["verified"])
        self.assertTrue(sanitized.exists())

        fingerprint_report = self.root / "fingerprint.json"
        result = self.run_cli(
            "fingerprint", self.source, sanitized, "--report", fingerprint_report, "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        fingerprint_payload = json.loads(result.stdout)
        self.assertEqual(fingerprint_payload["files_scanned"], 2)
        self.assertEqual(fingerprint_payload["candidate_group_count"], 1)
        details = json.loads(fingerprint_report.read_text(encoding="utf-8"))
        self.assertTrue(details["similar_candidates"])
        self.assertIn(details["similar_candidates"][0]["classification"], {"likely", "possible"})

    def test_md5_rotate_subcommand_and_plan_option_change_file_identity_only(self):
        rotated = self.root / "sample-md5-rotated.mp4"
        report = self.root / "md5-rotate.json"
        result = self.run_cli("md5-rotate", self.source, "--output", rotated, "--report", report, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "md5_rotated")
        self.assertTrue(payload["verified"])
        details = json.loads(report.read_text(encoding="utf-8"))
        self.assertNotEqual(details["input"]["md5"], details["output"]["md5"])
        self.assertNotEqual(details["input"]["sha256"], details["output"]["sha256"])
        self.assertEqual(details["output"]["video_codec"], "h264")
        self.assertEqual(details["output"]["audio_codec"], "aac")
        rotated_media = probe_media(rotated)
        self.assertEqual(rotated_media["width"], 320)
        self.assertEqual(rotated_media["height"], 240)

        plan = self.root / "md5-plan.json"
        output = self.root / "md5-output.mp4"
        result = self.run_cli(
            "plan", self.source, "--md5-rotate", "--final-output", output, "--output", plan, "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        saved_plan = json.loads(plan.read_text(encoding="utf-8"))
        md5_op = next(item for item in saved_plan["operations"] if item["type"] == "md5_rotate")
        self.assertEqual(md5_op["mode"], "metadata")

        execution = self.root / "md5-execution.json"
        result = self.run_cli("apply", plan, "--report", execution, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        execution_data = json.loads(execution.read_text(encoding="utf-8"))
        self.assertIn("md5_rotate", execution_data["operations_executed"])
        self.assertIn("md5_rotated", [check["name"] for check in execution_data["checks"]])
        self.assertNotEqual(
            execution_data["md5_rotate"]["input_md5"],
            execution_data["md5_rotate"]["output_md5"],
        )

        verification = self.root / "md5-verification.json"
        result = self.run_cli("verify", plan, "--report", verification, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        verify_data = json.loads(verification.read_text(encoding="utf-8"))
        self.assertTrue(verify_data["verified"])
        md5_check = next(check for check in verify_data["checks"] if check["name"] == "md5_rotated")
        self.assertEqual(md5_check["status"], "pass")

    def test_detect_accept_apply_and_verify_black_silent_tail(self):
        source = self.root / "tail-sample.mp4"
        process = subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=320x240:d=3:r=24",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x240:d=2:r=24",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono:d=2",
                "-filter_complex",
                "[0:v][1:a][2:v][3:a]concat=n=2:v=1:a=1[v][a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)

        analysis = self.root / "tail-analysis.json"
        result = self.run_cli("analyze", source, "--source", "generic", "--report", analysis, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        analysis_data = json.loads(analysis.read_text(encoding="utf-8"))
        suggestion = analysis_data["findings"]["suggested_intervals"][0]
        self.assertEqual(suggestion["type"], "junk_tail")
        self.assertGreater(suggestion["start"], 2.5)
        self.assertLess(suggestion["start"], 3.5)
        self.assertIn("dark", suggestion["evidence"])

        plan = self.root / "tail-plan.json"
        output = self.root / "tail-output.mp4"
        result = self.run_cli(
            "plan",
            source,
            "--analysis",
            analysis,
            "--accept-suggested-tail",
            "--final-output",
            output,
            "--output",
            plan,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        execution = self.root / "execution.json"
        result = self.run_cli("apply", plan, "--report", execution, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.exists())
        self.assertEqual(json.loads(result.stdout)["status"], "verified")

    def test_apply_core_visual_audio_and_region_transforms(self):
        plan = self.root / "transform-plan.json"
        output = self.root / "transform-output.mp4"
        result = self.run_cli(
            "plan",
            self.source,
            "--composition",
            "fit",
            "--resolution",
            "240x320",
            "--mirror",
            "--denoise",
            "light",
            "--color",
            "natural",
            "--filter",
            "cinematic",
            "--sharpen",
            "light",
            "--speed",
            "1.25",
            "--remove-region",
            "10:10:40:30",
            "--confirm-authorized-removal",
            "--approve-preview",
            "--approve-conflicts",
            "--final-output",
            output,
            "--output",
            plan,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

        execution = self.root / "transform-execution.json"
        result = self.run_cli("apply", plan, "--report", execution, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        execution_data = json.loads(execution.read_text(encoding="utf-8"))
        self.assertEqual(execution_data["status"], "verified")
        self.assertEqual(execution_data["output"]["width"], 240)
        self.assertEqual(execution_data["output"]["height"], 320)
        self.assertIn("mirror", execution_data["operations_executed"])
        self.assertIn("remove_region", execution_data["operations_executed"])
        self.assertAlmostEqual(execution_data["output"]["duration"], 0.8, delta=0.12)
        sync_check = next(check for check in execution_data["checks"] if check["name"] == "av_sync_duration")
        self.assertEqual(sync_check["status"], "pass")

        verification = self.root / "tail-verification.json"
        result = self.run_cli("verify", plan, "--report", verification, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "verified")


if __name__ == "__main__":
    unittest.main()
