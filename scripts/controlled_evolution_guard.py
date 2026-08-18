#!/usr/bin/env python3
"""Block unapproved learning-driven changes to a Skill's protected core."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

CANDIDATE_PREFIXES = (
    ".learnings/",
    "governance/proposals/",
    "governance/approvals/",
)
REQUIRED_APPROVAL_FIELDS = {
    "status",
    "approved_by",
    "approved_at",
    "approval_reference",
    "scope",
    "reason",
    "risk",
    "rollback",
    "tests",
}
SKILL_DIR = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def is_protected(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    return not any(normalized.startswith(prefix) for prefix in CANDIDATE_PREFIXES)


def repo_root(repo: Path) -> Path:
    return Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def skill_prefix(repo: Path) -> Path:
    root = repo_root(repo)
    try:
        return SKILL_DIR.resolve().relative_to(root)
    except ValueError:
        return Path()


def to_repo_path(repo: Path, skill_path: str) -> str:
    prefix = skill_prefix(repo)
    return (prefix / skill_path).as_posix() if prefix.parts else skill_path


def changed_paths(repo: Path, base_ref: str) -> set[str]:
    raw_paths: set[str] = set()
    root = repo_root(repo)
    for args in (
        ("diff", "--name-only", base_ref),
        ("diff", "--cached", "--name-only", base_ref),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        result = git(root, *args)
        raw_paths.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    prefix = skill_prefix(repo)
    if not prefix.parts:
        return raw_paths
    normalized: set[str] = set()
    for path in raw_paths:
        candidate = Path(path)
        try:
            normalized.add(candidate.relative_to(prefix).as_posix())
        except ValueError:
            continue
    return normalized


def exists_at_base(repo: Path, base_ref: str, path: str) -> bool:
    repo_path = to_repo_path(repo, path)
    return git(repo_root(repo), "cat-file", "-e", f"{base_ref}:{repo_path}", check=False).returncode == 0


def approval_at_base(repo: Path, base_ref: str, approval_path: str) -> bool:
    if not approval_path.startswith("governance/approvals/"):
        return False
    if not exists_at_base(repo, base_ref, approval_path):
        return False
    repo_path = to_repo_path(repo, approval_path)
    return git(repo_root(repo), "diff", "--quiet", base_ref, "--", repo_path, check=False).returncode == 0


def validate_approval(path: Path, protected: set[str]) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取批准记录: {exc}"]
    missing = sorted(REQUIRED_APPROVAL_FIELDS - data.keys())
    if missing:
        errors.append("缺少字段: " + ", ".join(missing))
    if data.get("status") != "approved":
        errors.append("status 必须为 approved")
    if not str(data.get("approved_by", "")).startswith("human:"):
        errors.append("approved_by 必须使用 human:NAME，Agent 不得自批")
    scope = data.get("scope")
    if not isinstance(scope, list) or not all(isinstance(item, str) for item in scope):
        errors.append("scope 必须是文件路径数组")
    else:
        uncovered = sorted(protected - set(scope))
        if uncovered:
            errors.append("批准范围未覆盖: " + ", ".join(uncovered))
    tests = data.get("tests")
    if not isinstance(tests, list) or not tests:
        errors.append("tests 必须列出至少一个验证")
    for field in ("approved_at", "approval_reference", "reason", "risk", "rollback"):
        if not str(data.get(field, "")).strip():
            errors.append(f"{field} 不能为空")
    return errors


def audit(
    repo: Path,
    base_ref: str,
    approval_file: str | None,
    bootstrap: bool,
) -> tuple[bool, dict[str, object]]:
    changed = changed_paths(repo, base_ref)
    protected = {path for path in changed if is_protected(path)}
    result: dict[str, object] = {
        "base_ref": base_ref,
        "changed": sorted(changed),
        "protected": sorted(protected),
        "mode": "normal",
    }
    if not protected:
        return True, result
    guard_path = "scripts/controlled_evolution_guard.py"
    if bootstrap:
        if exists_at_base(repo, base_ref, guard_path):
            result["errors"] = ["bootstrap 仅允许在基线尚无守卫脚本时使用"]
            return False, result
        result["mode"] = "bootstrap"
        return True, result
    if not approval_file:
        result["errors"] = ["受保护核心有变化，但未提供预先提交的人工批准记录"]
        return False, result
    approval_rel = str(Path(approval_file).as_posix()).lstrip("./")
    if not approval_at_base(repo, base_ref, approval_rel):
        result["errors"] = ["批准记录必须位于 governance/approvals/、已存在于基线且本次未修改"]
        return False, result
    errors = validate_approval(repo / approval_rel, protected)
    if errors:
        result["errors"] = errors
        return False, result
    result["approval_file"] = approval_rel
    return True, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--approval-file")
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    ok, result = audit(args.repo.resolve(), args.base_ref, args.approval_file, args.bootstrap)
    result["status"] = "ok" if ok else "blocked"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
