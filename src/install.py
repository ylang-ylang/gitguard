#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mermaid import PolicyParseError, load_policy_from_markdown


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_SCOPES = ("worktree", "local", "global")
DEFAULT_CONFIG = {
    "pre_push": {
        "auto_push_missing_tags": True,
    },
    "runtime": {
        "auto_sync": True,
    },
    "worktree": {
        "reject_branch_creation_in_linked_worktree": True,
    },
}


class InstallError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install git-guard hooks into a Git repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=available_configs_summary(),
    )
    parser.add_argument("--repo", default=".", help="Target Git repository working tree. Default: current directory.")
    parser.add_argument(
        "--config",
        required=True,
        help="Config name under configs/, config directory containing contribution.md, or direct contribution.md path.",
    )
    parser.add_argument(
        "--scope",
        choices=VALID_SCOPES,
        default="local",
        help="Where to write Git core.hooksPath: worktree, local, or global. Default: local.",
    )
    args = parser.parse_args()
    return run_install_command(repo=args.repo, config=args.config, scope=args.scope, parser=parser)


def available_configs_summary() -> str:
    config_root = PROJECT_ROOT / "configs"
    lines = ["Available bundled configs:"]

    if not config_root.exists():
        return "\n".join([*lines, f"  (configs directory not found: {config_root})"])

    configs = sorted(path for path in config_root.iterdir() if (path / "contribution.md").is_file())
    if not configs:
        return "\n".join([*lines, "  (none found)"])

    for config_dir in configs:
        contribution_path = config_dir / "contribution.md"
        lines.append(f"  {config_dir.name}: {format_config_branches(contribution_path)}")
    return "\n".join(lines)


def format_config_branches(contribution_path: Path) -> str:
    try:
        policy = load_policy_from_markdown(contribution_path)
    except PolicyParseError as exc:
        return f"cannot parse contribution.md ({exc})"

    branches = policy.get("branches", {})
    long_lived = branches.get("long_lived", [])
    families = branches.get("families", [])
    all_branches = [*long_lived, *families]
    if not all_branches:
        return "branches: (none)"
    return "branches: " + ", ".join(all_branches)


def run_install_command(repo: str | Path, config: str | Path, scope: str, parser: argparse.ArgumentParser) -> int:
    try:
        install(repo=Path(repo), config=config, scope=scope)
    except (PolicyParseError, InstallError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


def install(repo: Path, config: str | Path, scope: str = "local", runner: Path | None = None) -> None:
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
    policy = load_policy_from_markdown(contribution_path)

    install_dir = repo / ".git-guard"
    hook_dir = repo / ".git-guard" / "hooks"
    runtime_dir = repo / ".git-guard" / "runtime"
    hook_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    changes: list[str] = []

    installed_contribution_path = install_dir / "contribution.md"
    record_change(
        changes,
        ".git-guard/contribution.md",
        copy_file_if_different(contribution_path, installed_contribution_path),
    )
    policy_source = policy.setdefault("source", {})
    policy_source["path"] = installed_contribution_path.relative_to(repo).as_posix()
    policy_source.pop("original_path", None)

    runtime_runner = runtime_dir / "policy_reference_transaction_hook.py"
    if runner_path:
        changed = copy_file_if_different(runner_path, runtime_runner, mode=0o755)
    else:
        changed = write_text_if_different(runtime_runner, load_runtime_hook_text(), mode=0o755)
    record_change(changes, ".git-guard/runtime/policy_reference_transaction_hook.py", changed)

    policy_path = install_dir / "policy.json"
    config_path = install_dir / "config.json"
    state_path = git_dir / "git-guard-state.json"
    log_path = git_dir / "git-guard-hook.log"
    record_change(
        changes,
        ".git-guard/policy.json",
        write_text_if_different(policy_path, json.dumps(policy, indent=2, sort_keys=True) + "\n"),
    )
    record_change(changes, ".git-guard/config.json", ensure_config_defaults(config_path))

    hook_path = hook_dir / "reference-transaction"
    record_change(
        changes,
        ".git-guard/hooks/reference-transaction",
        write_text_if_different(hook_path, reference_transaction_hook(), mode=0o755),
    )

    pre_push_path = hook_dir / "pre-push"
    record_change(
        changes,
        ".git-guard/hooks/pre-push",
        write_text_if_different(pre_push_path, pre_push_hook(), mode=0o755),
    )

    enable_path = install_dir / "enable.sh"
    record_change(changes, ".git-guard/enable.sh", write_text_if_different(enable_path, enable_script(), mode=0o755))

    record_change(changes, "core.hooksPath", configure_hooks_path(repo, scope))

    if os.environ.get("GG_RUNTIME_SYNC_ACTIVE") == "1":
        report_runtime_auto_sync(changes)
        return

    print(f"installed git-guard hook into {repo}")
    print(f"scope={scope}")
    print("core.hooksPath=.git-guard/hooks")
    print(f"contribution={installed_contribution_path}")
    print(f"policy={policy_path}")
    print(f"config={config_path}")
    print(f"state={state_path}")
    print(f"log={log_path}")
    print(f"enable={enable_path}")


def record_change(changes: list[str], label: str, changed: bool) -> None:
    if changed:
        changes.append(label)


def report_runtime_auto_sync(changes: list[str]) -> None:
    if not changes:
        return
    print(
        "git-guard: runtime auto-sync updated installed assets: " + ", ".join(changes),
        file=sys.stderr,
    )


def default_config_text() -> str:
    return json.dumps(DEFAULT_CONFIG, indent=2, sort_keys=True) + "\n"


def ensure_config_defaults(path: Path) -> bool:
    if not path.exists():
        return write_text_if_different(path, default_config_text())

    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"config is not valid JSON: {path}\n{exc}") from exc
    except OSError as exc:
        raise InstallError(f"cannot read config: {path}\n{exc}") from exc

    if not isinstance(config, dict):
        raise InstallError(f"config must be a JSON object: {path}")

    merged = merge_defaults(config, DEFAULT_CONFIG)
    if merged != config:
        return write_text_if_different(path, json.dumps(merged, indent=2, sort_keys=True) + "\n")
    return False


def merge_defaults(config: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    for key, default_value in defaults.items():
        current_value = merged.get(key)
        if isinstance(current_value, dict) and isinstance(default_value, dict):
            merged[key] = merge_defaults(current_value, default_value)
        elif key not in merged:
            merged[key] = dict(default_value) if isinstance(default_value, dict) else default_value
    return merged


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
            'export GG_REPO_PATH="$repo_root"',
            'export GG_POLICY_JSON="$repo_root/.git-guard/policy.json"',
            'export GG_CONFIG_JSON="$repo_root/.git-guard/config.json"',
            'export GG_STATE_JSON="$resolved_git_dir/git-guard-state.json"',
            'export GG_LOG_PATH="$resolved_git_dir/git-guard-hook.log"',
            'runtime="$repo_root/.git-guard/runtime/policy_reference_transaction_hook.py"',
            *runtime_sync_shell_function(),
            'if [ "${1:-}" = "prepared" ]; then',
            "  git_guard_runtime_sync",
            "fi",
            '[ -f "$runtime" ] && [ -f "$GG_POLICY_JSON" ] || exit 0',
            'exec python3 "$runtime" "$@"',
            "",
        ]
    )


def runtime_sync_shell_function() -> list[str]:
    return [
        "git_guard_runtime_sync() {",
        '  [ "${GG_RUNTIME_SYNC_ACTIVE:-}" != "1" ] || return 0',
        '  [ -f "$GG_CONFIG_JSON" ] || return 0',
        "  python3 -c 'import json,sys; data=json.load(open(sys.argv[1], encoding=\"utf-8\")); runtime=data.get(\"runtime\", {}); value=runtime.get(\"auto_sync\", True) if isinstance(runtime, dict) else True; sys.exit(0 if value is not False else 1)' \"$GG_CONFIG_JSON\" || return 0",
        '  git_guard_command="${GIT_GUARD_BIN:-git-guard}"',
        '  read -r -a git_guard_args <<< "$git_guard_command"',
        '  command -v "${git_guard_args[0]}" >/dev/null 2>&1 || { printf "%s\\n" "git-guard: runtime auto-sync skipped; git-guard command not found" >&2; return 0; }',
        '  scope="$(git config --show-scope --get core.hooksPath 2>/dev/null | awk \'NR == 1 { print $1 }\')" || scope=""',
        '  case "$scope" in',
        '    worktree|local|global) ;;',
        '    *) scope="local" ;;',
        "  esac",
        '  if ! GG_RUNTIME_SYNC_ACTIVE=1 "${git_guard_args[@]}" install --repo "$repo_root" --config "$repo_root/.git-guard/contribution.md" --scope "$scope" >/dev/null; then',
        '    printf "%s\\n" "git-guard: runtime auto-sync failed; continuing with installed runtime" >&2',
        "  fi",
        "}",
    ]


def pre_push_hook() -> str:
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
            'export GG_REPO_PATH="$repo_root"',
            'export GG_POLICY_JSON="$repo_root/.git-guard/policy.json"',
            'export GG_CONFIG_JSON="$repo_root/.git-guard/config.json"',
            'export GG_STATE_JSON="$resolved_git_dir/git-guard-state.json"',
            'export GG_LOG_PATH="$resolved_git_dir/git-guard-hook.log"',
            'runtime="$repo_root/.git-guard/runtime/policy_reference_transaction_hook.py"',
            *runtime_sync_shell_function(),
            "git_guard_runtime_sync",
            '[ -f "$runtime" ] && [ -f "$GG_POLICY_JSON" ] || exit 0',
            'exec python3 "$runtime" pre-push "$@"',
            "",
        ]
    )


def enable_script() -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -eu",
            'repo_root="$(git rev-parse --show-toplevel)"',
            'cd "$repo_root"',
            "git config --local core.hooksPath .git-guard/hooks",
            'printf "%s\\n" "enabled git-guard hooks for repository $repo_root"',
            'printf "%s\\n" "core.hooksPath=.git-guard/hooks"',
            "",
        ]
    )


def configure_hooks_path(repo: Path, scope: str) -> bool:
    args = ["config"]
    changed = False
    if scope == "worktree":
        changed = ensure_worktree_config(repo)
        args.append("--worktree")
    elif scope == "local":
        args.append("--local")
    elif scope == "global":
        args.append("--global")
    current = git(repo, *args, "--get", "core.hooksPath", check=False)
    if current.returncode == 0 and current.stdout.strip() == ".git-guard/hooks":
        return changed
    args.extend(["core.hooksPath", ".git-guard/hooks"])
    git(repo, *args)
    return True


def ensure_worktree_config(repo: Path) -> bool:
    current = git(repo, "config", "--local", "--get", "extensions.worktreeConfig", check=False)
    if current.returncode == 0 and current.stdout.strip() == "true":
        return False
    git(repo, "config", "extensions.worktreeConfig", "true")
    return True


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


def load_runtime_hook_text() -> str:
    return (Path(__file__).resolve().parent / "runtime" / "reference_transaction_hook.py").read_text(encoding="utf-8")


def copy_file_if_different(source: Path, target: Path, mode: int | None = None) -> bool:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        if mode is not None:
            return ensure_mode(target, mode)
        return False

    try:
        content = source.read_bytes()
    except OSError as exc:
        raise InstallError(f"cannot read source file: {source}\n{exc}") from exc
    return write_bytes_if_different(target, content, mode=mode)


def write_text_if_different(path: Path, content: str, mode: int | None = None) -> bool:
    return write_bytes_if_different(path, content.encode("utf-8"), mode=mode)


def write_bytes_if_different(path: Path, content: bytes, mode: int | None = None) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = read_bytes_or_none(path)
    current_mode_matches = mode is None or (path.exists() and (path.stat().st_mode & 0o777) == mode)
    if current == content and current_mode_matches:
        return False

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with open(fd, "wb", closefd=True) as stream:
            stream.write(content)
        if mode is not None:
            temp_path.chmod(mode)
        else:
            temp_path.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
        temp_path.replace(path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise InstallError(f"cannot write file: {path}\n{exc}") from exc
    return True


def read_bytes_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InstallError(f"cannot read file: {path}\n{exc}") from exc


def ensure_mode(path: Path, mode: int) -> bool:
    if (path.stat().st_mode & 0o777) != mode:
        path.chmod(mode)
        return True
    return False


def resolved_git_dir(repo: Path) -> Path:
    raw = Path(git(repo, "rev-parse", "--git-dir").stdout.strip())
    if not raw.is_absolute():
        raw = repo / raw
    return raw.resolve()


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise InstallError(f"git {' '.join(args)} failed in {repo}\n{result.stderr.strip()}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
