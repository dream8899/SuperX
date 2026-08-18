#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "wechat_channels_register.py"
SPEC = importlib.util.spec_from_file_location("wechat_channels_register", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class WeChatChannelsRegisterTests(unittest.TestCase):
    def test_share_id_accepts_only_canonical_share_url(self) -> None:
        self.assertEqual(MODULE.share_id("https://weixin.qq.com/sph/AyKQ5kUyuU"), "AyKQ5kUyuU")
        with self.assertRaises(ValueError):
            MODULE.share_id("https://finder.video.qq.com/temporary-token")

    def test_safe_name_removes_path_characters(self) -> None:
        self.assertEqual(MODULE.safe_name("a/b:c", "fallback"), "a-b-c")

    def test_metadata_is_newest_first_and_resequenced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.tsv"
            rows = [
                {"发布日期": "2026-01-01", "短码": "old"},
                {"发布日期": "2026-02-01", "短码": "new"},
            ]
            MODULE.write_rows(path, rows)
            with path.open(encoding="utf-8", newline="") as handle:
                saved = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual([row["短码"] for row in saved], ["new", "old"])
            self.assertEqual([row["序号"] for row in saved], ["001", "002"])


if __name__ == "__main__":
    unittest.main()
