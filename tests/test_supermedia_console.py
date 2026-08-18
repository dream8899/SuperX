#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from supermedia_console import dashboard, discover_platforms, update_catalog


class SuperMediaConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        creator = self.root / "instagram" / "maker"
        creator.mkdir(parents=True)
        (creator / "001__ABC123.mp4").write_bytes(b"source-video")
        (creator / "metadata.tsv").write_text(
            "短码\t文件名\t标题\nABC123\t001__ABC123.mp4\tDemo\n", encoding="utf-8"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_update_discovers_platform_backs_up_and_exports(self):
        self.assertEqual(discover_platforms(self.root), ["instagram"])
        result = update_catalog(self.root)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertTrue(Path(result["reports"]["summary"]).is_file())
        last_update = self.root / ".supermedia" / "reports" / "last_update.json"
        self.assertEqual(json.loads(last_update.read_text(encoding="utf-8"))["platforms"], ["instagram"])
        page = dashboard(self.root)
        self.assertEqual(page["summary"]["counts"]["creators"], 1)
        self.assertEqual(page["summary"]["counts"]["present_assets"], 1)
        self.assertEqual(page["creators"][0]["source_count"], 1)


if __name__ == "__main__":
    unittest.main()
