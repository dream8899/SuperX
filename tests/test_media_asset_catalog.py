#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from media_asset_catalog import Catalog, instagram_id_from_filename


class MediaAssetCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        creator = self.root / "instagram" / "maker"
        creator.mkdir(parents=True)
        (creator / "001__ABC123.mp4").write_bytes(b"source-video")
        (creator / "metadata.tsv").write_text(
            "序号\t发布日期\t短码\t视频地址\t标题\t状态\t文件名\t时长秒\t文件字节\t发现时间\t下载时间\t备注\n"
            "1\t2026-07-01\tABC123\thttps://www.instagram.com/reel/ABC123/\tDemo\t已验证\t"
            "001__ABC123.mp4\t1\t12\t2026-07-01\t2026-07-01\t\n",
            encoding="utf-8",
        )
        derivative = creator / "remix_20260731"
        derivative.mkdir()
        (derivative / "ABC123__r01.mp4").write_bytes(b"derived-video")
        self.catalog = Catalog(self.root)

    def tearDown(self):
        self.catalog.close()
        self.temporary.cleanup()

    def test_sync_preflight_reserve_complete_and_duplicate_block(self):
        stats = self.catalog.sync_platform("instagram")
        self.assertEqual(stats["sources"], 1)
        self.assertEqual(stats["source_assets"], 1)
        self.assertEqual(stats["derivative_assets"], 1)
        self.assertEqual(self.catalog.summary()["counts"]["present_assets"], 2)
        manifest = self.root / "batch.json"
        file_path = self.root / "instagram" / "maker" / "remix_20260731" / "ABC123__r01.mp4"
        manifest.write_text(
            json.dumps([{"file": str(file_path), "title": "Demo", "plan_order": 1}]),
            encoding="utf-8",
        )
        report = self.catalog.preflight_manifest(manifest, "tencent", "account-a")
        self.assertEqual(report["summary"], {"items": 1, "allow": 1, "review": 0, "block": 0})
        self.catalog.reserve_manifest(manifest, "tencent", "account-a")
        result = self.catalog.complete_manifest(
            manifest,
            "tencent",
            "account-a",
            "draft_saved_verified",
            "draft_box_title",
        )
        self.assertEqual(result, {"recorded": 1, "unresolved": 0})
        duplicate = self.catalog.preflight_manifest(manifest, "tencent", "account-a")
        self.assertEqual(duplicate["summary"]["block"], 1)

    def test_unknown_derivative_is_held(self):
        creator = self.root / "instagram" / "maker"
        unknown_dir = creator / "unknown_batch"
        unknown_dir.mkdir()
        (unknown_dir / "mystery.mp4").write_bytes(b"mystery")
        stats = self.catalog.sync_platform("instagram")
        self.assertEqual(stats["lineage_hold"], 1)
        summary = self.catalog.summary()
        self.assertEqual(summary["open_blocking_warnings"], 1)
        linked = self.catalog.link_asset(
            unknown_dir / "mystery.mp4",
            "instagram:ABC123",
            batch_id="unknown_batch",
            evidence="test review",
            confidence=1.0,
        )
        self.assertEqual(linked["lineage_status"], "manual_reviewed")
        self.assertEqual(self.catalog.summary()["open_blocking_warnings"], 0)

    def test_repairs_legacy_instagram_suffix_without_slug_bleed(self):
        self.assertEqual(
            instagram_id_from_filename(
                "006_building-pink-white-edition-ul_DayEqKhERXu.mp4"
            ),
            "DayEqKhERXu",
        )
        self.assertEqual(
            instagram_id_from_filename(
                "008__by-macigbox-studio_Da09-UwsT5M__h264-aac.mp4"
            ),
            "Da09-UwsT5M",
        )
        self.assertEqual(
            instagram_id_from_filename("041_DaiMWCQTw-H_h264-aac.mp4"),
            "DaiMWCQTw-H",
        )


if __name__ == "__main__":
    unittest.main()
