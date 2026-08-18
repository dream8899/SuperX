#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "controlled_evolution_guard.py"
SPEC = importlib.util.spec_from_file_location("controlled_evolution_guard", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


class ControlledEvolutionGuardTests(unittest.TestCase):
    def make_repo(self) -> Path:
        root = Path(self.addCleanupDir.name)
        git(root, "init", "-q")
        git(root, "config", "user.email", "guard@example.invalid")
        git(root, "config", "user.name", "Guard Test")
        (root / "SKILL.md").write_text("core-v1\n", encoding="utf-8")
        git(root, "add", "SKILL.md")
        git(root, "commit", "-qm", "baseline")
        return root

    def setUp(self) -> None:
        self.addCleanupDir = tempfile.TemporaryDirectory()
        self.addCleanup(self.addCleanupDir.cleanup)

    def test_learning_candidate_is_not_protected(self) -> None:
        self.assertFalse(MODULE.is_protected(".learnings/candidate.md"))
        self.assertTrue(MODULE.is_protected("SKILL.md"))
        self.assertTrue(MODULE.is_protected("scripts/tool.py"))

    def test_core_change_without_approval_is_blocked(self) -> None:
        repo = self.make_repo()
        (repo / "SKILL.md").write_text("core-v2\n", encoding="utf-8")
        ok, result = MODULE.audit(repo, "HEAD", None, False)
        self.assertFalse(ok)
        self.assertEqual(result["protected"], ["SKILL.md"])

    def test_precommitted_human_approval_allows_exact_scope(self) -> None:
        repo = self.make_repo()
        approval = repo / "governance/approvals/change.json"
        approval.parent.mkdir(parents=True)
        approval.write_text(json.dumps({
            "status": "approved",
            "approved_by": "human:owner",
            "approved_at": "2026-08-03T00:00:00Z",
            "approval_reference": "explicit maintenance request",
            "scope": ["SKILL.md"],
            "reason": "test",
            "risk": "test risk",
            "rollback": "git revert",
            "tests": ["unit test"],
        }), encoding="utf-8")
        git(repo, "add", "governance/approvals/change.json")
        git(repo, "commit", "-qm", "approve")
        (repo / "SKILL.md").write_text("core-v2\n", encoding="utf-8")
        ok, result = MODULE.audit(
            repo, "HEAD", "governance/approvals/change.json", False
        )
        self.assertTrue(ok, result)

    def test_bootstrap_is_blocked_after_guard_is_in_baseline(self) -> None:
        repo = self.make_repo()
        guard = repo / "scripts/controlled_evolution_guard.py"
        guard.parent.mkdir(parents=True)
        guard.write_text("guard\n", encoding="utf-8")
        git(repo, "add", "scripts/controlled_evolution_guard.py")
        git(repo, "commit", "-qm", "install guard")
        (repo / "SKILL.md").write_text("core-v2\n", encoding="utf-8")
        ok, _ = MODULE.audit(repo, "HEAD", None, True)
        self.assertFalse(ok)

    def test_nested_skill_paths_are_normalized(self) -> None:
        repo = self.make_repo()
        skill = repo / "nested-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("nested-v1\n", encoding="utf-8")
        git(repo, "add", "nested-skill/SKILL.md")
        git(repo, "commit", "-qm", "nested baseline")
        old_skill_dir = MODULE.SKILL_DIR
        MODULE.SKILL_DIR = skill
        self.addCleanup(setattr, MODULE, "SKILL_DIR", old_skill_dir)
        (skill / "SKILL.md").write_text("nested-v2\n", encoding="utf-8")
        (skill / "references").mkdir()
        (skill / "references/new.md").write_text("new\n", encoding="utf-8")
        self.assertEqual(
            MODULE.changed_paths(skill, "HEAD"),
            {"SKILL.md", "references/new.md"},
        )


if __name__ == "__main__":
    unittest.main()
