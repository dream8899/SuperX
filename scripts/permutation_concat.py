#!/usr/bin/env python3
"""Permutation concatenation with first-position exclusion.

Rule: Given N split segments, generate 2-segment and 3-segment concatenations.
      Each segment may appear in the FIRST position at most ONCE across all output.
      Segment 01 is excluded from first position entirely.

Usage:
  python3 permutation_concat.py --input-dir DIR [--types 2,3] [--output-dir DIR]
"""

import argparse, subprocess, sys
from itertools import permutations
from pathlib import Path


def run(input_dir: Path, output_dir: Path, types: list[int] = [2, 3],
        exclude_first: str = "01"):
    """Generate and execute permutation concat plan."""
    mp4s = sorted(input_dir.glob("*.mp4"))
    if len(mp4s) < 2:
        print("至少需要2个视频")
        return

    # Build segment registry
    segs = {f.stem[:2]: f for f in mp4s if f.stem[:2].isdigit()}
    tags = sorted(segs.keys())
    print(f"素材段: {len(tags)} 个 ({', '.join(tags)})")

    plans = {}
    for n in types:
        plans[n] = []
        used_first = set()

        for first_tag in tags:
            if first_tag == exclude_first:
                continue
            if first_tag in used_first:
                continue

            # Try to build a valid n-segment permutation
            remaining = [t for t in tags if t != first_tag]
            for combo in permutations(remaining, n - 1):
                key = tuple([first_tag] + list(combo))
                plans[n].append(key)
                used_first.add(first_tag)
                break  # one per first-segment

    # Show plan
    total = 0
    for n in types:
        print(f"\n{n}段拼接: {len(plans[n])} 个")
        for combo in plans[n]:
            ids = "+".join(combo)
            names = "+".join(segs[t].stem[3:][:12] for t in combo)
            print(f"  [{ids}] {names}")
        total += len(plans[n])

    print(f"\n合计: {total} 个")

    # Execute
    output_dir.mkdir(parents=True, exist_ok=True)
    for n in types:
        sub_dir = output_dir / f"{n}段"
        sub_dir.mkdir(exist_ok=True)
        for combo in plans[n]:
            ids = "+".join(combo)
            names = "+".join(segs[t].stem[3:][:15] for t in combo)
            out = sub_dir / f"{ids}_{names}.mp4"
            concat_list = "\n".join(f"file '{segs[t].resolve()}'" for t in combo)
            list_file = sub_dir / f".concat_{ids}.txt"
            list_file.write_text(concat_list)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                 "-i", str(list_file), "-c", "copy", str(out)],
                check=True, timeout=60,
            )
            list_file.unlink()

    print(f"\n✅ 输出: {output_dir}")


def main():
    p = argparse.ArgumentParser(description="排列组合拼接——首位去重")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--types", default="2,3", help="拼接类型，逗号分隔 (默认: 2,3)")
    p.add_argument("--exclude-first", default="01", help="排除的序号 (默认: 01)")
    args = p.parse_args()

    types = [int(t) for t in args.types.split(",")]
    run(Path(args.input_dir), Path(args.output_dir), types, args.exclude_first)


if __name__ == "__main__":
    raise SystemExit(main())
