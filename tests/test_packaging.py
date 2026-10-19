"""Consumer boundaries: both distribution formats work without the checkout."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ("dev-only", "dev-feat-case", "dev-feat-release-hotfix-case", "dev-infra-feat-release-hotfix-case")


class InstalledDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="git-guard-packaging-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "GIT_", "GG_"))}
        home = cls.root / "home"
        home.mkdir()
        cls.env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
        cls.version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        cls.command(sys.executable, str(ROOT / "tools/build_release.py"), "--repo-root", str(ROOT), "--out-dir", str(cls.root / "dist"))
        cls.environments = []
        for suffix, name in (("tar.gz", "sdist"), ("whl", "wheel")):
            artifact, = (cls.root / "dist").glob(f"*.{suffix}")
            venv = cls.root / name
            cls.command(sys.executable, "-m", "venv", str(venv))
            python = venv / "bin/python"
            cls.command(str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", str(artifact))
            cls.environments.append((name, venv, python, venv / "bin/git-guard"))

    @classmethod
    def command(cls, *args, cwd=None, check=True):
        result = subprocess.run(args, cwd=cwd or cls.root, env=cls.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check and result.returncode:
            raise AssertionError(f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
        return result

    def test_installed_help_and_named_policies_without_checkout(self):
        for name, venv, python, cli in self.environments:
            with self.subTest(distribution=name):
                self.assertEqual(self.command(str(cli), "--version").stdout.strip(), f"git-guard {self.version}")
                help_text = self.command(str(cli), "--help").stdout
                resource_root = Path(self.command(str(python), "-I", "-c", "import install; print(install.bundled_config_root())").stdout.strip())
                self.assertTrue(resource_root.is_relative_to(venv))
                for config in CONFIGS:
                    self.assertIn(f"  {config}: branches:", help_text)
                    repo = self.root / f"{name}-{config}"
                    self.command("git", "init", "-b", "main", str(repo))
                    self.assertFalse((repo / ".git-guard").exists())
                    self.assertNotEqual(self.command("git", "config", "--get", "core.hooksPath", cwd=repo, check=False).returncode, 0)
                    self.command("git", "-c", "user.name=Package Test", "-c", "user.email=package@example.invalid",
                                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "initial", cwd=repo)
                    self.command(str(cli), "install", "--repo", str(repo), "--config", config)
                    self.assertEqual(self.command("git", "config", "--local", "--get", "core.hooksPath", cwd=repo).stdout.strip(), ".git-guard/hooks")
                    rejected = self.command("git", "branch", "forbidden/package-probe", cwd=repo, check=False)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("BRANCH_NAME_NOT_ALLOWED", rejected.stderr)
                    self.assertNotEqual(self.command("git", "show-ref", "--verify", "refs/heads/forbidden/package-probe",
                                                     cwd=repo, check=False).returncode, 0)

    def test_installed_hooks_and_state_preservation(self):
        for name, venv, python, cli in self.environments:
            with self.subTest(distribution=name):
                self.command(sys.executable, str(ROOT / "tools/smoke_install.py"),
                             "--python", str(python), "--version", self.version)


if __name__ == "__main__":
    unittest.main()
