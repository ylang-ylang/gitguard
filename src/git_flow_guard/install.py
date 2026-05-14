#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.resources
import json
import os
import shutil
import subprocess
from pathlib import Path

from git_flow_guard.generate import render_policy_yaml
from git_flow_guard.mermaid import PolicyParseError, load_policy_from_markdown


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VALID_SCOPES = ("worktree", "local", "global")


class InstallError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Install git-flow-guard hooks into a Git repository.")
    parser.add_argument("--repo", default=".", help="Target Git repository working tree. Default: current directory.")
    parser.add_argument(
        "--config",
        required=True,
        help="Config name under configs/, config directory containing contribution.md, or direct contribution.md path.",
    )
    parser.add_argument(
        "--scope",
        choices=VALID_SCOPES,
        default="worktree",
        help="Where to write Git core.hooksPath: worktree, local, or global. Default: worktree.",
    )
    args = parser.parse_args()
    return run_install_command(repo=args.repo, config=args.config, scope=args.scope, parser=parser)


def run_install_command(repo: str | Path, config: str | Path, scope: str, parser: argparse.ArgumentParser) -> int:
    try:
        install(repo=Path(repo), config=config, scope=scope)
    except (PolicyParseError, InstallError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


def install(repo: Path, config: str | Path, scope: str = "worktree", runner: Path | None = None) -> None:
    if scope not in VALID_SCOPES:
        raise InstallError(f"invalid scope {scope!r}; expected one of {', '.join(VALID_SCOPES)}")

    repo = repo.resolve()
    runner_path = Path(runner).resolve() if runner else None
    if runner_path and not runner_path.exists():
        raise InstallError(f"hook runner does not exist: {runner_path}")

    worktree = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if worktree != repo:
        raise InstallError(f"{repo} is not the Git worktree root: {worktree}")

    git_dir = resolved_git_dir(repo)
    contribution_path = resolve_config(config)
    rendered_policy_yaml = render_policy_yaml(contribution_path)
    policy = load_policy_from_markdown(contribution_path)

    install_dir = repo / ".git-flow-guard"
    hook_dir = repo / ".git-flow-guard" / "hooks"
    runtime_dir = repo / ".git-flow-guard" / "runtime"
    hook_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    installed_contribution_path = install_dir / "contribution.md"
    shutil.copyfile(contribution_path, installed_contribution_path)
    policy.setdefault("source", {})["path"] = display_policy_path(installed_contribution_path)
    policy["source"]["original_path"] = display_policy_path(contribution_path)

    runtime_runner = runtime_dir / "policy_reference_transaction_hook.py"
    if runner_path:
        shutil.copyfile(runner_path, runtime_runner)
    else:
        runtime_runner.write_text(load_runtime_hook_text(), encoding="utf-8")
    runtime_runner.chmod(0o755)

    policy_path = git_dir / "git-flow-guard-policy.json"
    policy_yaml_path = repo / ".git-flow-guard" / "policy.yaml"
    state_path = git_dir / "git-flow-guard-state.json"
    log_path = git_dir / "git-flow-guard-hook.log"
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True), encoding="utf-8")
    policy_yaml_path.write_text(rendered_policy_yaml, encoding="utf-8")

    hook_path = hook_dir / "reference-transaction"
    hook_path.write_text(reference_transaction_hook(), encoding="utf-8")
    hook_path.chmod(0o755)

    configure_hooks_path(repo, scope)
    print(f"installed git-flow-guard hook into {repo}")
    print(f"scope={scope}")
    print("core.hooksPath=.git-flow-guard/hooks")
    print(f"contribution={installed_contribution_path}")
    print(f"generated={policy_yaml_path}")
    print(f"policy={policy_path}")
    print(f"state={state_path}")
    print(f"log={log_path}")


def reference_transaction_hook() -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -eu",
            'repo_root="$(git rev-parse --show-toplevel)"',
            'git_dir="$(git rev-parse --git-dir)"',
            'case "$git_dir" in',
            '  /*) resolved_git_dir="$git_dir" ;;',
            '  *) resolved_git_dir="$repo_root/$git_dir" ;;',
            "esac",
            'export GFG_REPO_PATH="$repo_root"',
            'export GFG_POLICY_JSON="$resolved_git_dir/git-flow-guard-policy.json"',
            'export GFG_STATE_JSON="$resolved_git_dir/git-flow-guard-state.json"',
            'export GFG_LOG_PATH="$resolved_git_dir/git-flow-guard-hook.log"',
            'exec python3 "$repo_root/.git-flow-guard/runtime/policy_reference_transaction_hook.py" "$@"',
            "",
        ]
    )


def configure_hooks_path(repo: Path, scope: str) -> None:
    args = ["config"]
    if scope == "worktree":
        ensure_worktree_config(repo)
        args.append("--worktree")
    elif scope == "local":
        args.append("--local")
    elif scope == "global":
        args.append("--global")
    args.extend(["core.hooksPath", ".git-flow-guard/hooks"])
    git(repo, *args)


def ensure_worktree_config(repo: Path) -> None:
    git(repo, "config", "extensions.worktreeConfig", "true")


def resolve_config(config: str | Path) -> Path:
    raw = Path(config)
    candidates = []
    if raw.is_absolute() or raw.exists():
        candidates.append(raw)
    else:
        candidates.append(PROJECT_ROOT / "configs" / str(config))
        candidates.append(PROJECT_ROOT / str(config))

    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.is_file():
            return candidate
        contribution = candidate / "contribution.md"
        if contribution.exists():
            return contribution

    raise InstallError(f"cannot resolve config: {config}")


def display_policy_path(path: Path) -> str:
    display_root = os.environ.get("GFG_POLICY_DISPLAY_ROOT")
    if not display_root:
        return str(path)

    source_root = Path(os.environ.get("GFG_POLICY_SOURCE_ROOT", str(PROJECT_ROOT))).resolve()
    try:
        relative = path.resolve().relative_to(source_root)
    except ValueError:
        return str(path)
    return str(Path(display_root) / relative)


def load_runtime_hook_text() -> str:
    resource = importlib.resources.files("git_flow_guard").joinpath("runtime/reference_transaction_hook.py")
    return resource.read_text(encoding="utf-8")


def resolved_git_dir(repo: Path) -> Path:
    raw = Path(git(repo, "rev-parse", "--git-dir").stdout.strip())
    if not raw.is_absolute():
        raw = repo / raw
    return raw.resolve()


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise InstallError(f"git {' '.join(args)} failed in {repo}\n{result.stderr.strip()}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
