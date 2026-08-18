#!/usr/bin/env python3
"""Synchronize this skill to existing local installations without deleting files."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def is_target(path: Path) -> bool:
    marker = path / "SKILL.md"
    if not marker.is_file():
        return False
    first = marker.read_text(encoding="utf-8", errors="ignore")[:500]
    return "name: superdown88" in first


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def sync(source: Path, destination: Path) -> dict[str, object]:
    changed: list[str] = []
    for item in source.rglob("*"):
        if not item.is_file() or ".git" in item.parts or "__pycache__" in item.parts:
            continue
        relative = item.relative_to(source)
        target = destination / relative
        if target.exists() and digest(item) == digest(target):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        changed.append(str(relative))
    return {"destination": str(destination), "changed": changed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--root", action="append", type=Path, default=[], help="Agent skill root; repeatable")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    roots = args.root or [Path.home() / ".codex" / "skills", Path.home() / ".agents" / "skills"]
    targets = sorted({marker.parent for root in roots if root.is_dir() for marker in root.rglob("SKILL.md") if marker.parent.resolve() != source and is_target(marker.parent)})
    reports = []
    for target in targets:
        if args.dry_run:
            reports.append({"destination": str(target), "changed": "dry-run"})
        else:
            reports.append(sync(source, target))
    print(json.dumps({"source": str(source), "targets": reports}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
