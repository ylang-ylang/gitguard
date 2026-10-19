#!/usr/bin/env python3
"""Exercise an installed distribution and real Git hooks, never a source import."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

CONFIGS = ("dev-only", "dev-feat-case", "dev-feat-release-hotfix-case", "dev-infra-feat-release-hotfix-case")


def smoke(python: str, version: str, artifact: Path | None = None) -> None:
    executable = shutil.which(python)
    if executable is None:
        raise ValueError(f"Python executable not found: {python}")
    # Keep the venv's lexical bin path: resolving its python symlink loses it.
    executable = str(Path(executable).absolute())
    if artifact is not None:
        artifact = artifact.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="git-guard-smoke-") as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_", "GG_", "PYTHON")) and key not in ("VIRTUAL_ENV", "GIT_GUARD_BIN")
        }
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")

        def run(*command: str, cwd: Path = root, check: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
            result = subprocess.run(command, cwd=cwd, env=env, input=input, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if check and result.returncode:
                raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}\n{result.stderr}")
            return result

        repo = root / "repo"
        repo.mkdir()

        def git(*args: str, **kwargs) -> subprocess.CompletedProcess:
            return run("git", *args, cwd=repo, **kwargs)

        git("init", "-b", "main")
        git("config", "user.name", "git-guard package smoke")
        git("config", "user.email", "git-guard-smoke@example.invalid")
        if artifact is not None:
            venv = root / "venv"
            run(executable, "-m", "venv", str(venv))
            executable = str(venv / "bin/python")
            run(executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", str(artifact))
        cli = Path(executable).parent / "git-guard"
        if not cli.is_file():
            raise ValueError(f"installed CLI is missing beside Python: {cli}")
        env["PATH"] = str(cli.parent) + os.pathsep + env.get("PATH", os.defpath)
        if (repo / ".git-guard").exists() or git("config", "--get", "core.hooksPath", check=False).returncode == 0:
            raise AssertionError("package installation must not activate repository hooks")
        result = run(str(cli), "--version")
        if result.stdout.strip() != f"git-guard {version}":
            raise AssertionError(f"unexpected CLI version: {result.stdout!r}")
        for args in (("--help",), ("install", "--help")):
            help_text = run(str(cli), *args).stdout
            for config in CONFIGS:
                if f"  {config}: branches:" not in help_text:
                    raise AssertionError(f"installed help is missing bundled config {config}")
        runtime_path = run(executable, "-I", "-c", "import runtime.auto_push; print(runtime.auto_push.__file__)").stdout.strip()
        if not Path(runtime_path).is_file():
            raise AssertionError("installed auto_push runtime is missing")
        (repo / "initial.txt").write_text("initial\n", encoding="utf-8")
        git("add", "initial.txt")
        git("commit", "-m", "initial")
        initial = git("rev-parse", "HEAD").stdout.strip()
        git("checkout", "-b", "dev")
        run(str(cli), "install", "--repo", str(repo), "--config", "dev-only")
        config_path = repo / ".git-guard/config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["auto_push"]["timeout_seconds"] = 7
        config["package_smoke_user_setting"] = {"preserve": True}
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not (repo / ".git-guard/runtime/auto_push.py").is_file():
            raise AssertionError("installer omitted auto_push support module")
        for path in (repo / ".git-guard").rglob("*"):
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                if "/Cellar/" in content or str(cli.parent) in content or runtime_path in content:
                    raise AssertionError(f"nonportable installation path embedded in {path}")
        git("add", ".git-guard", ".branch_logs")
        git("commit", "-m", "install packaged hooks on dev")
        result = git("update-ref", "-d", "refs/heads/main", initial, check=False)
        if result.returncode == 0 or "PROTECTED_REF_DELETE" not in result.stdout + result.stderr:
            raise AssertionError(f"real protected-ref deletion was not rejected: {result.stdout}\n{result.stderr}")
        if git("rev-parse", "main").stdout.strip() != initial:
            raise AssertionError("rejected update changed the protected ref")
        (repo / "allowed.txt").write_text("allowed dev change\n", encoding="utf-8")
        git("add", "allowed.txt")
        missing_log = git("commit", "-m", "missing branch log", check=False)
        if missing_log.returncode == 0 or "BRANCH_LOG_CHANGE_REQUIRED" not in missing_log.stdout + missing_log.stderr:
            raise AssertionError("installed hook did not enforce a branch-log update")
        log = repo / ".branch_logs/dev.md"
        log.write_text(log.read_text(encoding="utf-8") + "- Packaged runtime allowed dev commit.\n", encoding="utf-8")
        git("add", ".branch_logs/dev.md")
        git("commit", "-m", "allowed dev change with branch log")
        dev = git("rev-parse", "dev").stdout.strip()
        git("checkout", "main")
        git("merge", "--no-ff", "--no-commit", "dev")
        main_log = repo / ".branch_logs/main.md"
        main_log.write_text("# main\n\n<!-- git-guard: ref=refs/heads/main -->\n\n- Merge validated dev change.\n", encoding="utf-8")
        git("add", ".branch_logs/main.md")
        git("commit", "-m", "merge dev through allowed policy flow")
        if git("rev-parse", "HEAD^2").stdout.strip() != dev:
            raise AssertionError("allowed flow did not produce the expected merge")
        hook_log = repo / ".git/git-guard-hook.log"
        log_text = hook_log.read_text(encoding="utf-8")
        for expected in ("hook=pre-commit", "hook=prepared result=ok", "hook=post-commit result=ok", "result=rejected code=PROTECTED_REF_DELETE", "BRANCH_LOG_CHANGE_REQUIRED"):
            if expected not in log_text:
                raise AssertionError(f"installed hook log is missing {expected}")
        preserved = [config_path, repo / ".git-guard/contribution.md", repo / ".git/git-guard-state.json", hook_log]
        before = {path: path.read_bytes() for path in preserved}
        head = git("rev-parse", "HEAD").stdout
        index = git("write-tree").stdout
        run(str(cli), "install", "--repo", str(repo), "--config", str(repo / ".git-guard/contribution.md"))
        for path, content in before.items():
            if path.read_bytes() != content:
                raise AssertionError(f"reinstall changed user configuration/state: {path.name}")
        if git("rev-parse", "HEAD").stdout != head or git("write-tree").stdout != index:
            raise AssertionError("reinstall changed repository HEAD/index")
        if git("status", "--porcelain").stdout.strip():
            raise AssertionError("reinstall unexpectedly changed checked-in assets")
        print(f"isolated installed CLI {version}: four configs, runtime, protected rejection, branch logs, allowed merge, portable hooks, state-preserving reinstall OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args()
    try:
        smoke(args.python, args.version, args.artifact)
    except (ValueError, OSError, RuntimeError, AssertionError) as exc:
        parser.exit(1, f"smoke failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
