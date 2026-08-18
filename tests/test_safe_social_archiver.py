#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "safe_social_archiver.py"
SPEC = importlib.util.spec_from_file_location("safe_social_archiver", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceTests(unittest.TestCase):
    def test_strips_tracking_and_classifies_instagram_video(self) -> None:
        clean = MODULE.validate_public_url("https://www.instagram.com/reel/Ab_c-12/?igsh=tracking")
        self.assertEqual(clean, "https://www.instagram.com/reel/Ab_c-12/")
        self.assertEqual(MODULE.classify_source(clean)["source_type"], "video")

    def test_canonicalizes_username_prefixed_reel(self) -> None:
        clean = MODULE.validate_public_url("https://www.instagram.com/example/reel/Abc123/?igsh=x")
        self.assertEqual(clean, "https://www.instagram.com/reel/Abc123/")

    def test_classifies_instagram_profile(self) -> None:
        source = MODULE.classify_source("https://www.instagram.com/example/")
        self.assertEqual(source, {"platform": "instagram", "source_type": "profile", "host": "instagram.com"})

    def test_classifies_username_prefixed_instagram_reel(self) -> None:
        source = MODULE.classify_source("https://www.instagram.com/example/reel/Abc123/")
        self.assertEqual(source["source_type"], "video")

    def test_classifies_tiktok_and_youtube(self) -> None:
        self.assertEqual(MODULE.classify_source("https://www.tiktok.com/@name/video/123/")["source_type"], "video")
        self.assertEqual(MODULE.classify_source("https://www.tiktok.com/@name/")["source_type"], "profile")
        self.assertEqual(MODULE.classify_source("https://youtu.be/abc/")["source_type"], "video")
        self.assertEqual(MODULE.classify_source("https://www.youtube.com/@name/")["source_type"], "profile")

    def test_preserves_youtube_video_id_but_removes_tracking(self) -> None:
        clean = MODULE.validate_public_url("https://www.youtube.com/watch?v=abc123&utm_source=test&list=PL1")
        self.assertEqual(clean, "https://www.youtube.com/watch?v=abc123&list=PL1")
        self.assertEqual(MODULE.classify_source(clean)["source_type"], "video")

    def test_rejects_local_sources(self) -> None:
        for url in ("http://localhost/video", "http://127.0.0.1/video", "https://printer.local/video"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                MODULE.validate_public_url(url)

    def test_normalizes_agent_output_and_deduplicates(self) -> None:
        text = """
        found https://www.instagram.com/name/reel/Abc123/?igsh=x),
        duplicate https://www.instagram.com/reel/Abc123/
        and https://youtu.be/video42?t=10
        """
        self.assertEqual(
            MODULE.extract_discovered_urls(text),
            ["https://www.instagram.com/reel/Abc123/", "https://youtu.be/video42"],
        )

    def test_normalizes_json_shortcodes_when_explicitly_enabled(self) -> None:
        text = '{"codes": ["Abc123", "Def_456", "Abc123", "not valid"]}'
        self.assertEqual(
            MODULE.extract_discovered_urls(text, "creator"),
            [
                "https://www.instagram.com/reel/Abc123/",
                "https://www.instagram.com/reel/Def_456/",
            ],
        )

    def test_reads_sources_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sources.txt"
            path.write_text("https://www.instagram.com/reel/Abc123/?x=1\n", encoding="utf-8")
            self.assertEqual(MODULE.read_sources_file(path), ["https://www.instagram.com/reel/Abc123/"])

    def test_archive_parser_supports_bounded_sources_file_batches(self) -> None:
        args = MODULE.build_parser().parse_args([
            "archive", "--sources-file", "sources.txt", "--source-offset", "5",
            "--output-dir", "/tmp/archive", "--max-items", "20",
        ])
        self.assertEqual(args.sources, [])
        self.assertEqual(args.source_offset, 5)
        self.assertEqual(args.max_items, 20)

    def test_instagram_username_accepts_profile_and_rejects_post(self) -> None:
        self.assertEqual(MODULE.instagram_username("@demo.name"), "demo.name")
        self.assertEqual(MODULE.instagram_username("https://www.instagram.com/demo.name/"), "demo.name")
        with self.assertRaises(ValueError):
            MODULE.instagram_username("https://www.instagram.com/reel/Abc123/")

    def test_incremental_reel_discovery_stops_at_known_anchor(self) -> None:
        posts = [SimpleNamespace(shortcode=value) for value in ("New111", "Known22", "Older33")]
        urls, stopped_at = MODULE.select_new_instagram_reels(posts, {"Known22"}, 5, True)
        self.assertEqual(urls, ["https://www.instagram.com/reel/New111/"])
        self.assertEqual(stopped_at, "Known22")

    def test_backfill_can_scan_past_known_anchor(self) -> None:
        posts = [SimpleNamespace(shortcode=value) for value in ("New111", "Known22", "Older33")]
        urls, stopped_at = MODULE.select_new_instagram_reels(posts, {"Known22"}, 5, False)
        self.assertEqual(
            urls,
            ["https://www.instagram.com/reel/New111/", "https://www.instagram.com/reel/Older33/"],
        )
        self.assertIsNone(stopped_at)

    def test_loads_known_codes_from_metadata_and_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metadata.tsv"
            path.write_text(
                "序号\t发布日期\t短码\t标题\n001\t\tAbc123\tOne\n"
                "https://www.instagram.com/reel/Def_456/\n",
                encoding="utf-8",
            )
            self.assertEqual(MODULE.load_known_instagram_codes([path]), {"Abc123", "Def_456"})

    def test_extractor_capabilities_do_not_confuse_single_with_profile(self) -> None:
        capabilities = MODULE.extractor_capabilities({
            "Instagram", "InstagramIOS", "instagram:user (CURRENTLY BROKEN)",
            "youtube:tab", "TikTok",
        })
        self.assertTrue(capabilities["instagram_single"])
        self.assertFalse(capabilities["instagram_profile"])
        self.assertTrue(capabilities["youtube_channel"])

    def test_working_instagram_profile_extractor_is_detected(self) -> None:
        capabilities = MODULE.extractor_capabilities({"Instagram", "instagram:user"})
        self.assertTrue(capabilities["instagram_profile"])

    def test_discover_instagram_command_with_fake_public_adapter(self) -> None:
        captured_options: dict[str, object] = {}

        class FakeLoader:
            def __init__(self, **options: object) -> None:
                captured_options.update(options)
                self.context = object()

        class FakeProfileResult:
            def get_reels(self) -> list[SimpleNamespace]:
                return [SimpleNamespace(shortcode="New111"), SimpleNamespace(shortcode="New222")]

        class FakeProfile:
            @staticmethod
            def from_username(context: object, username: str) -> FakeProfileResult:
                self = FakeProfileResult()
                self.username = username
                return self

        fake_module = SimpleNamespace(Instaloader=FakeLoader, Profile=FakeProfile)
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict("sys.modules", {"instaloader": fake_module}):
            output = Path(temp_dir) / "sources.txt"
            args = argparse.Namespace(
                profile="creator", max_items=5, known=[], scan_past_known=False,
                request_timeout=20, output=output,
            )
            self.assertEqual(MODULE.command_discover_instagram(args), 0)
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                ["https://www.instagram.com/reel/New111/", "https://www.instagram.com/reel/New222/"],
            )
            self.assertFalse(captured_options["download_videos"])
            self.assertEqual(captured_options["max_connection_attempts"], 1)


class SafetyTests(unittest.TestCase):
    def test_failure_categories(self) -> None:
        self.assertEqual(MODULE.classify_failure("HTTP Error 429: Too Many Requests"), "rate_limit")
        self.assertEqual(MODULE.classify_failure("challenge_required"), "challenge")
        self.assertEqual(MODULE.classify_failure("Connection timed out"), "transient")
        self.assertEqual(MODULE.classify_failure("SSL: UNEXPECTED_EOF_WHILE_READING"), "transient")
        self.assertEqual(MODULE.cooldown_seconds("transient"), 900)
        self.assertEqual(MODULE.cooldown_seconds("rate_limit"), 3600)
        self.assertEqual(MODULE.cooldown_seconds("challenge"), 86400)

    def test_native_collection_command_has_no_login_and_zero_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = argparse.Namespace(
                output_dir=temp_dir,
                socket_timeout=20,
                sleep_requests=1.0,
                max_items=38,
                min_sleep=12.0,
                max_sleep=18.0,
                dry_run=False,
            )
            sources = [{"url": "https://www.youtube.com/@example/", "platform": "youtube", "source_type": "profile", "host": "youtube.com"}]
            command = MODULE.build_ytdlp_command(args, sources)
            joined = " ".join(command)
            self.assertIn("--ignore-config", command)
            self.assertIn("--no-cookies", command)
            self.assertNotIn("--cookies-from-browser", command)
            self.assertIn("--lazy-playlist", command)
            self.assertIn(":38", command)
            self.assertGreaterEqual(joined.count(" 0"), 4)

    def test_instaloader_discovery_options_never_download_or_login(self) -> None:
        options = MODULE.instaloader_discovery_options(20)
        self.assertFalse(options["download_pictures"])
        self.assertFalse(options["download_videos"])
        self.assertFalse(options["save_metadata"])
        self.assertEqual(options["max_connection_attempts"], 1)
        self.assertIn(429, options["fatal_status_codes"])
        self.assertNotIn("login", options)
        self.assertNotIn("sessionfile", options)

    def test_archive_rejects_instagram_profile_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = argparse.Namespace(
                sources=["https://www.instagram.com/example/"],
                sources_file=[],
                source_offset=0,
                output_dir=temp_dir,
                socket_timeout=20,
                command_timeout=30,
                sleep_requests=1.0,
                max_items=5,
                min_sleep=12.0,
                max_sleep=18.0,
                dry_run=True,
            )
            with self.assertRaisesRegex(ValueError, "discover-instagram"):
                MODULE.command_archive(args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
