"""Exercise installed hooks against real, initialized Git submodules."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from install import install
from runtime.common import HookReject, RefUpdate, ZERO
from runtime.submodule_policy import enforce_submodule_main_guard


class SubmodulePathTests(unittest.TestCase):
    name = 'assets/lib.v1+punct "quoted"'
    old_path = "assets/lib with space"
    new_path = "data/assets/lib with space"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="git-guard-submodule-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("GIT_", "GG_"))}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Guard Test", GIT_AUTHOR_EMAIL="guard@example.invalid",
                        GIT_COMMITTER_NAME="Guard Test", GIT_COMMITTER_EMAIL="guard@example.invalid")
        environment = patch.dict(os.environ, self.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.remote = self.root / "remote"
        self.repo = self.root / "superproject"
        for repo in (self.remote, self.repo):
            self.command(repo.parent, "init", "-b", "main", str(repo))
            self.command(repo, "commit", "--allow-empty", "-m", "initial")
        self.base = self.command(self.remote, "rev-parse", "HEAD").stdout.strip()
        self.command(self.remote, "commit", "--allow-empty", "-m", "allowed tip")
        self.pin = self.command(self.remote, "rev-parse", "HEAD").stdout.strip()
        self.command(self.remote, "checkout", "-b", "side")
        self.command(self.remote, "commit", "--allow-empty", "-m", "not on main")
        self.side = self.command(self.remote, "rev-parse", "HEAD").stdout.strip()
        self.command(self.remote, "checkout", "main")
        self.command(self.repo, "-c", "protocol.file.allow=always", "submodule", "add",
                     "--name", self.name, str(self.remote), self.old_path)
        self.command(self.repo, "commit", "-am", "register old path")
        self.original_modules = (self.repo / ".gitmodules").read_bytes()

    def command(self, repo, *args, check=True, input=None):
        result = subprocess.run(["git", "-C", str(repo), *args], env=self.env, input=input,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check and result.returncode:
            self.fail(f"git {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
        return result

    def target(self, *, pin=None, mapping="valid"):
        self.command(self.repo, "config", "--file", ".gitmodules", f"submodule.{self.name}.path", self.new_path)
        if mapping == "missing":
            self.command(self.repo, "config", "--file", ".gitmodules", "--remove-section", f"submodule.{self.name}")
        elif mapping == "ambiguous":
            self.command(self.repo, "config", "--file", ".gitmodules", "submodule.other.path", self.new_path)
        elif mapping == "duplicate-name":
            self.command(self.repo, "config", "--file", ".gitmodules", "--add", f"submodule.{self.name}.path", "other/path")
        self.command(self.repo, "add", ".gitmodules")
        self.command(self.repo, "update-index", "--force-remove", self.old_path)
        self.command(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{pin or self.pin},{self.new_path}")
        tree = self.command(self.repo, "write-tree").stdout.strip()
        parent = self.command(self.repo, "rev-parse", "HEAD").stdout.strip()
        target = self.command(self.repo, "commit-tree", tree, "-p", parent, "-m", "same module relocated").stdout.strip()
        self.command(self.repo, "read-tree", "HEAD")
        (self.repo / ".gitmodules").write_bytes(self.original_modules)
        return target

    def install_guard(self, target):
        self.command(self.repo, "branch", "dev", target)
        with contextlib.redirect_stdout(io.StringIO()):
            install(self.repo, ROOT / "configs/dev-feat-case/contribution.md")
        self.assertEqual(self.command(self.repo, "config", "--get", "core.hooksPath").stdout.strip(), ".git-guard/hooks")

    def create(self, target, *, rejection=None):
        self.install_guard(target)
        result = self.command(self.repo, "branch", "feat/relocated", "dev", check=False)
        if rejection:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(rejection, result.stderr)
            self.assertNotEqual(self.command(self.repo, "show-ref", "--verify", "refs/heads/feat/relocated", check=False).returncode, 0)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.command(self.repo, "rev-parse", "feat/relocated").stdout.strip(), target)
        return result

    def test_same_name_relocated_fixed_pin_accepted(self):
        target = self.target()
        self.create(target)
        self.assertFalse((self.repo / self.new_path).exists())
        self.assertTrue((self.repo / self.old_path / ".git").is_file())
        self.assertEqual((self.repo / ".gitmodules").read_bytes(), self.original_modules)

    def test_allowed_ancestor_accepted_with_existing_warning(self):
        result = self.create(self.target(pin=self.base))
        self.assertIn("SUBMODULE_BEHIND_ORIGIN_MAIN", result.stderr)

    def test_missing_real_worktree_rejected_despite_parent_discovery(self):
        target = self.target()
        self.command(self.repo, "submodule", "deinit", "-f", "--", self.old_path)
        self.create(target, rejection="SUBMODULE_REPO_MISSING")

    def test_unrelated_real_repository_at_registered_path_rejected(self):
        target = self.target()
        self.command(self.repo, "submodule", "deinit", "-f", "--", self.old_path)
        self.command(self.repo, "clone", "--separate-git-dir", str(self.root / "unrelated-admin"),
                     str(self.remote), self.old_path)
        self.create(target, rejection="SUBMODULE_REPO_MISSING")

    def test_old_form_embedded_repository_accepted(self):
        self.repo = self.root / "embedded-superproject"
        self.command(self.root, "init", "-b", "main", str(self.repo))
        self.command(self.repo, "commit", "--allow-empty", "-m", "initial")
        self.command(self.repo, "clone", str(self.remote), self.old_path)
        self.command(self.repo, "-c", "protocol.file.allow=always", "submodule", "add",
                     "--name", self.name, str(self.remote), self.old_path)
        self.command(self.repo, "commit", "-am", "register embedded module")
        self.original_modules = (self.repo / ".gitmodules").read_bytes()
        self.assertTrue((self.repo / self.old_path / ".git").is_dir())
        self.create(self.target())

    def test_missing_fixed_object_rejected(self):
        self.create(self.target(pin="1" * 40), rejection="SUBMODULE_COMMIT_MISSING")

    def test_disallowed_ancestry_rejected(self):
        self.create(self.target(pin=self.side), rejection="SUBMODULE_COMMIT_NOT_ALLOWED")

    def test_missing_allowed_refs_rejected(self):
        target = self.target()
        submodule = self.repo / self.old_path
        self.command(submodule, "checkout", "--detach")
        self.command(submodule, "branch", "-D", "main")
        self.command(submodule, "branch", "-rd", "origin/main")
        self.create(target, rejection="SUBMODULE_ALLOWED_BRANCH_REF_MISSING")

    def test_missing_target_mapping_rejected(self):
        self.create(self.target(mapping="missing"), rejection="SUBMODULE_REPO_MISSING")

    def test_ambiguous_target_path_rejected(self):
        self.create(self.target(mapping="ambiguous"), rejection="SUBMODULE_REPO_MISSING")

    def test_ambiguous_target_name_rejected(self):
        self.create(self.target(mapping="duplicate-name"), rejection="SUBMODULE_REPO_MISSING")

    def test_missing_current_mapping_rejected(self):
        target = self.target()
        self.command(self.repo, "config", "--file", ".gitmodules", "--remove-section", f"submodule.{self.name}")
        self.create(target, rejection="SUBMODULE_REPO_MISSING")

    def test_ambiguous_current_path_rejected(self):
        target = self.target()
        self.command(self.repo, "config", "--file", ".gitmodules", "submodule.other.path", self.old_path)
        self.create(target, rejection="SUBMODULE_REPO_MISSING")

    def test_same_pin_in_another_commit_still_checks_mapping(self):
        valid = self.target()
        invalid = self.target(mapping="missing")
        with self.assertRaises(HookReject) as rejected:
            enforce_submodule_main_guard(self.repo, {"submodules": {"main_guard": True, "allowed_branches": ["main"]}},
                                         [RefUpdate(ZERO, valid, "refs/heads/feat/valid"),
                                          RefUpdate(ZERO, invalid, "refs/heads/feat/invalid")])
        self.assertEqual(rejected.exception.code, "SUBMODULE_REPO_MISSING")

    def test_linked_superproject_uses_its_own_registered_gitdir(self):
        target = self.target()
        linked = self.root / "linked"
        self.command(self.repo, "worktree", "add", "-b", "linked", str(linked), "main")
        self.command(linked, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
        enforce_submodule_main_guard(linked, {"submodules": {"main_guard": True, "allowed_branches": ["main"]}},
                                     [RefUpdate(ZERO, target, "refs/heads/feat/relocated")])
        actual = Path(self.command(linked / self.old_path, "rev-parse", "--absolute-git-dir").stdout.strip())
        self.assertTrue(actual.is_relative_to(linked.parent / "superproject/.git/worktrees"))


if __name__ == "__main__":
    unittest.main()
