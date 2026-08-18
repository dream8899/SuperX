#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "template_a.py"
SPEC = importlib.util.spec_from_file_location("template_a", MODULE_PATH)
assert SPEC and SPEC.loader
template_a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(template_a)


class TemplateACatalogTests(unittest.TestCase):
    def test_infers_video_download_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Video_Download"
            directory = root / "instagram" / "creator" / "remix"
            directory.mkdir(parents=True)
            self.assertEqual(template_a.infer_catalog_root(directory), root.resolve())

    def test_explicit_catalog_cli_must_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "media_asset_catalog.py"
            script.write_text("# test\n", encoding="utf-8")
            self.assertEqual(template_a.resolve_catalog_cli(script), script.resolve())


if __name__ == "__main__":
    unittest.main()
