from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar

from install import install as install_policy_hook
from install import load_runtime_hook_text
from mermaid import load_policy_from_markdown


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FINISH_SYMBOL = "========test finished========"
UNEXPECTED_ACCEPTANCE_SYMBOL = "!!!!!!!! GIT GUARD EXPECTED REJECTION WAS ACCEPTED !!!!!!!!"
EXPECTED_AGENT_HINT = "if you are an agent, read the contribution document and use the configured workflow; do not try to bypass this hook."


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class RefSnapshot:
    refs: dict[str, str]


class PolicyHookTestBase:
    config_name: ClassVar[str]

    def __init__(self, config_dir: Path, work_root: Path, keep: bool = False) -> None:
        self.config_dir = config_dir
        self.name = config_dir.name
        self.work_root = work_root / self.name
        self.repo = self.work_root
        self.keep = keep
        self.policy = load_policy_from_markdown(config_dir / "contribution.md")
        self.assert_dev_direct_commit_policy_matches_rules()

    def run(self) -> None:
        if self.work_root.exists() and not self.keep:
            shutil.rmtree(self.work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)

        self.create_initial_repo()
        self.install_hook()
        self.create_correct_git_dag_tree()
        self.create_rejection_test_fixtures()
        self.mark_rejection_tests_start()
        self.run_rejection_tests()
        self.checkout_final_branch()
        self.assert_submodule_main_guard()
        self.assert_linked_worktree_branch_creation_guard()

        print(f"PASS {self.name}: policy hook test repo is {self.repo}")
        print(f"{self.name}: {FINISH_SYMBOL}")

    def create_initial_repo(self) -> None:
        raise NotImplementedError

    def create_correct_git_dag_tree(self) -> None:
        raise NotImplementedError

    def create_rejection_test_fixtures(self) -> None:
        raise NotImplementedError

    def mark_rejection_tests_start(self) -> None:
        raise NotImplementedError

    def run_rejection_tests(self) -> None:
        raise NotImplementedError

    def checkout_final_branch(self) -> None:
        raise NotImplementedError

    def assert_dev_direct_commit_policy_matches_rules(self) -> None:
        rules = (self.config_dir / "contribution.md").read_text(encoding="utf-8")
        direct_commit_names = {item["name"] for item in self.policy.get("direct_commit_refs", [])}
        if "`dev` is the integration branch and must not receive direct commits" in rules and "dev" in direct_commit_names:
            raise AssertionError(f"{self.name}: rules forbid direct dev commits, but policy allows them")
        if "`dev` is the only development branch and may receive direct commits" in rules and "dev" not in direct_commit_names:
            raise AssertionError(f"{self.name}: rules allow direct dev commits, but policy rejects them")

    def install_hook(self) -> None:
        self.seed_default_branch_logs_before_install()
        install_policy_hook(self.repo, self.config_dir, scope="worktree")
        expected_files = [
            self.repo / ".git-guard" / "contribution.md",
            self.repo / ".git-guard" / "config.json",
            self.repo / ".git-guard" / "policy.json",
            self.repo / ".git-guard" / "enable.sh",
            self.repo / ".git-guard" / "hooks" / "reference-transaction",
            self.repo / ".git-guard" / "hooks" / "pre-push",
            self.repo / ".git-guard" / "hooks" / "pre-commit",
            self.repo / ".git-guard" / "runtime" / "policy_reference_transaction_hook.py",
        ]
        for path in expected_files:
            if not path.exists():
                raise AssertionError(f"{self.name}: installed file is missing: {path}")
        if (self.repo / ".git-guard" / "policy.yaml").exists():
            raise AssertionError(f"{self.name}: policy.yaml should not be installed")
        policy = json.loads((self.repo / ".git-guard" / "policy.json").read_text(encoding="utf-8"))
        source = policy.get("source", {})
        if source.get("path") != ".git-guard/contribution.md":
            raise AssertionError(f"{self.name}: policy source path should be repo-relative, got {source.get('path')!r}")
        if Path(source["path"]).is_absolute():
            raise AssertionError(f"{self.name}: policy source path should not be absolute: {source['path']}")
        if "original_path" in source:
            raise AssertionError(f"{self.name}: policy source should not include original_path: {source['original_path']}")
        config = json.loads((self.repo / ".git-guard" / "config.json").read_text(encoding="utf-8"))
        if config.get("branch_logs", {}).get("path") != ".branch_logs/":
            raise AssertionError(f"{self.name}: config should set default branch log path")
        if config.get("branch_logs", {}).get("force_required") is not True:
            raise AssertionError(f"{self.name}: config should force branch logs by default")
        if config.get("worktree", {}).get("reject_branch_creation_in_linked_worktree") is not True:
            raise AssertionError(f"{self.name}: config should enable linked worktree branch creation guard by default")
        if config.get("pre_push", {}).get("auto_push_missing_tags") is not True:
            raise AssertionError(f"{self.name}: config should enable missing tag auto-push by default")
        if config.get("runtime", {}).get("auto_sync") is not True:
            raise AssertionError(f"{self.name}: config should enable runtime auto-sync by default")
        if config.get("submodules", {}).get("allowed_branches") != ["main", "case/*/*"]:
            raise AssertionError(f"{self.name}: config should default submodule allowed branches to main and case/*/*")
        if config.get("submodules", {}).get("main_guard") is not True:
            raise AssertionError(f"{self.name}: config should enable submodule main guard by default")
        for hook_name in ["reference-transaction", "pre-push", "pre-commit"]:
            hook_text = (self.repo / ".git-guard" / "hooks" / hook_name).read_text(encoding="utf-8")
            if "git_guard_runtime_sync" not in hook_text:
                raise AssertionError(f"{self.name}: {hook_name} hook should include runtime auto-sync")
            if "GIT_GUARD_BIN" not in hook_text:
                raise AssertionError(f"{self.name}: {hook_name} hook should support GIT_GUARD_BIN")
        self.assert_install_is_idempotent_and_preserves_config()
        self.assert_local_install_clears_worktree_hooks_path_override()
        self.assert_runtime_auto_sync_repairs_installed_runtime()
        self.assert_runtime_auto_sync_can_be_disabled()
        self.git_no_hooks("config", "--worktree", "core.hooksPath", ".git-flow-guard/hooks")
        self.git_no_hooks("config", "--local", "--unset", "core.hooksPath", check=False)
        result = self.run_command([".git-guard/enable.sh"], check=True)
        if "core.hooksPath=.git-guard/hooks" not in result.stdout:
            raise AssertionError(f"{self.name}: enable.sh did not report hook path\nstdout:\n{result.stdout}")
        hook_path = self.git_no_hooks("config", "--local", "--get", "core.hooksPath").stdout.strip()
        if hook_path != ".git-guard/hooks":
            raise AssertionError(f"{self.name}: enable.sh set unexpected hooksPath: {hook_path}")
        worktree_hook_path = self.git_no_hooks("config", "--worktree", "--get", "core.hooksPath", check=False)
        if worktree_hook_path.returncode == 0:
            raise AssertionError(f"{self.name}: enable.sh should not set worktree hooksPath: {worktree_hook_path.stdout.strip()}")

    def assert_local_install_clears_worktree_hooks_path_override(self) -> None:
        self.git_no_hooks("config", "--worktree", "core.hooksPath", ".git-flow-guard/hooks")
        install_policy_hook(self.repo, self.repo / ".git-guard" / "contribution.md", scope="local")

        hook_path = self.git_no_hooks("config", "--local", "--get", "core.hooksPath").stdout.strip()
        if hook_path != ".git-guard/hooks":
            raise AssertionError(f"{self.name}: local install set unexpected hooksPath: {hook_path}")

        effective = self.git("config", "--show-scope", "--get", "core.hooksPath").stdout.strip()
        if effective != "local\t.git-guard/hooks":
            raise AssertionError(f"{self.name}: local install should be effective, got: {effective}")

        worktree_hook_path = self.git_no_hooks("config", "--worktree", "--get", "core.hooksPath", check=False)
        if worktree_hook_path.returncode == 0:
            raise AssertionError(f"{self.name}: local install should clear worktree hooksPath: {worktree_hook_path.stdout.strip()}")

        install_policy_hook(self.repo, self.repo / ".git-guard" / "contribution.md", scope="worktree")

    def assert_install_is_idempotent_and_preserves_config(self) -> None:
        config_path = self.repo / ".git-guard" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("runtime", {})["auto_sync"] = False
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        install_policy_hook(self.repo, self.repo / ".git-guard" / "contribution.md", scope="worktree")
        updated = json.loads(config_path.read_text(encoding="utf-8"))
        if updated.get("runtime", {}).get("auto_sync") is not False:
            raise AssertionError(f"{self.name}: reinstall should preserve runtime.auto_sync=false")
        if updated.get("branch_logs", {}).get("path") != ".branch_logs/":
            raise AssertionError(f"{self.name}: reinstall should preserve/add branch_logs.path")
        if updated.get("branch_logs", {}).get("force_required") is not True:
            raise AssertionError(f"{self.name}: reinstall should preserve/add branch_logs.force_required=true")
        if updated.get("pre_push", {}).get("auto_push_missing_tags") is not True:
            raise AssertionError(f"{self.name}: reinstall should preserve/add pre_push defaults")
        if updated.get("worktree", {}).get("reject_branch_creation_in_linked_worktree") is not True:
            raise AssertionError(f"{self.name}: reinstall should preserve/add worktree defaults")
        if updated.get("submodules", {}).get("allowed_branches") != ["main", "case/*/*"]:
            raise AssertionError(f"{self.name}: reinstall should preserve/add submodule allowed branch defaults")
        if updated.get("submodules", {}).get("main_guard") is not True:
            raise AssertionError(f"{self.name}: reinstall should preserve/add submodule main guard defaults")

        config.setdefault("runtime", {})["auto_sync"] = True
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def assert_runtime_auto_sync_repairs_installed_runtime(self) -> None:
        runtime_path = self.repo / ".git-guard" / "runtime" / "policy_reference_transaction_hook.py"
        runtime_path.write_text("#!/usr/bin/env python3\n# stale runtime sentinel\n", encoding="utf-8")
        runtime_path.chmod(0o755)

        result = self.git("branch", "forbidden/runtime-auto-sync", "HEAD", check=False)
        combined = result.stdout + result.stderr
        if "BRANCH_NAME_NOT_ALLOWED" not in combined:
            raise AssertionError(
                f"{self.name}: expected repaired runtime to enforce branch policy\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if "runtime auto-sync updated installed assets" not in combined:
            raise AssertionError(
                f"{self.name}: expected runtime auto-sync to print a visible update message\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if ".git-guard/runtime/policy_reference_transaction_hook.py" not in combined:
            raise AssertionError(
                f"{self.name}: expected runtime auto-sync message to name the repaired runtime asset\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if runtime_path.read_text(encoding="utf-8") != load_runtime_hook_text():
            raise AssertionError(f"{self.name}: runtime auto-sync did not repair installed runtime")

    def assert_runtime_auto_sync_can_be_disabled(self) -> None:
        config_path = self.repo / ".git-guard" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("runtime", {})["auto_sync"] = False
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        runtime_path = self.repo / ".git-guard" / "runtime" / "policy_reference_transaction_hook.py"
        stale_runtime = "#!/usr/bin/env python3\n# stale runtime sentinel disabled\n"
        runtime_path.write_text(stale_runtime, encoding="utf-8")
        runtime_path.chmod(0o755)

        result = self.git("branch", "forbidden/runtime-auto-sync-disabled", "HEAD", check=False)
        combined = result.stdout + result.stderr
        if "runtime auto-sync updated installed assets" in combined:
            raise AssertionError(
                f"{self.name}: disabled runtime auto-sync should not print an update message\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if "BRANCH_NAME_NOT_ALLOWED" in combined:
            raise AssertionError(
                f"{self.name}: runtime auto-sync ran even though config disabled it\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if runtime_path.read_text(encoding="utf-8") != stale_runtime:
            raise AssertionError(f"{self.name}: disabled runtime auto-sync should not repair runtime")
        self.git_no_hooks("branch", "-D", "forbidden/runtime-auto-sync-disabled", check=False)

        config.setdefault("runtime", {})["auto_sync"] = True
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        install_policy_hook(self.repo, self.repo / ".git-guard" / "contribution.md", scope="worktree")

    def expect_rejected(
        self,
        args: list[str],
        expected: str,
        cleanup: Callable[[], None] | None = None,
    ) -> CommandResult:
        before = self.ref_snapshot()
        result = self.git(*args, check=False)
        after = self.ref_snapshot()

        if result.returncode == 0:
            marker_sha = self.create_unexpected_acceptance_marker(args)
            raise AssertionError(
                f"{self.name}: expected rejection for git {' '.join(args)}, but it was accepted\n"
                f"failure marker: {marker_sha}\n"
                f"refs before: {json.dumps(before.refs, indent=2, sort_keys=True)}\n"
                f"refs after: {json.dumps(after.refs, indent=2, sort_keys=True)}"
            )

        combined = result.stdout + result.stderr
        if expected not in combined:
            raise AssertionError(
                f"{self.name}: expected rejection containing {expected!r} for git {' '.join(args)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        expected_policy_hint = f"see policy: {self.expected_policy_hint_path()}"
        if expected_policy_hint not in combined:
            raise AssertionError(
                f"{self.name}: expected rejection to include policy hint {expected_policy_hint!r} "
                f"for git {' '.join(args)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if EXPECTED_AGENT_HINT not in combined:
            raise AssertionError(
                f"{self.name}: expected rejection to include agent guidance {EXPECTED_AGENT_HINT!r} "
                f"for git {' '.join(args)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        if after.refs != before.refs:
            raise AssertionError(
                f"{self.name}: rejected git {' '.join(args)} changed refs\n"
                f"before: {json.dumps(before.refs, indent=2, sort_keys=True)}\n"
                f"after: {json.dumps(after.refs, indent=2, sort_keys=True)}"
            )

        if cleanup:
            cleanup()
        return result

    def expect_merge_rejected(self, source: str, expected: str) -> CommandResult:
        target_old = self.rev_parse("HEAD")
        before = self.ref_snapshot()
        result = self.git("merge", "--no-ff", "--no-edit", "--no-commit", "-X", "ours", source, check=False)
        if result.returncode != 0:
            conflicts = self.conflicted_paths()
            non_branch_log_conflicts = [path for path in conflicts if path != ".branch_logs" and not path.startswith(".branch_logs/")]
            if not conflicts or non_branch_log_conflicts:
                raise AssertionError(
                    f"{self.name}: merge setup for expected rejection failed before hook check\n"
                    f"non-branch-log conflicts: {non_branch_log_conflicts}\n"
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )
        self.restore_target_branch_log_tree(target_old)
        remaining_conflicts = self.conflicted_paths()
        if remaining_conflicts:
            raise AssertionError(f"{self.name}: unresolved merge conflicts before expected rejection: {remaining_conflicts}")

        result = self.git("commit", "-m", f"MR {source} expected rejection", check=False)
        after = self.ref_snapshot()
        combined = result.stdout + result.stderr

        if result.returncode == 0:
            marker_sha = self.create_unexpected_acceptance_marker(["merge", source])
            raise AssertionError(
                f"{self.name}: expected rejection for merge {source}, but it was accepted\n"
                f"failure marker: {marker_sha}\n"
                f"refs before: {json.dumps(before.refs, indent=2, sort_keys=True)}\n"
                f"refs after: {json.dumps(after.refs, indent=2, sort_keys=True)}"
            )
        if expected not in combined:
            raise AssertionError(
                f"{self.name}: expected rejection containing {expected!r} for merge {source}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        expected_policy_hint = f"see policy: {self.expected_policy_hint_path()}"
        if expected_policy_hint not in combined:
            raise AssertionError(
                f"{self.name}: expected rejection to include policy hint {expected_policy_hint!r} "
                f"for merge {source}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if EXPECTED_AGENT_HINT not in combined:
            raise AssertionError(
                f"{self.name}: expected rejection to include agent guidance for merge {source}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if after.refs != before.refs:
            raise AssertionError(
                f"{self.name}: rejected merge {source} changed refs\n"
                f"before: {json.dumps(before.refs, indent=2, sort_keys=True)}\n"
                f"after: {json.dumps(after.refs, indent=2, sort_keys=True)}"
            )
        self.cleanup_merge_state()
        if self.rev_parse("HEAD") != target_old:
            raise AssertionError(f"{self.name}: expected rejected merge cleanup to restore {target_old}")
        return result

    def create_unexpected_acceptance_marker(self, args: list[str]) -> str:
        self.git_no_hooks("merge", "--abort", check=False)
        marker_path = self.repo / "UNEXPECTED_ACCEPTANCE.txt"
        with marker_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{UNEXPECTED_ACCEPTANCE_SYMBOL}\n")
            stream.write(f"accepted command: git {' '.join(args)}\n")
        self.git_no_hooks("add", "UNEXPECTED_ACCEPTANCE.txt")
        self.git_no_hooks(
            "commit",
            "--allow-empty",
            "-m",
            f"{UNEXPECTED_ACCEPTANCE_SYMBOL}: git {' '.join(args)}",
        )
        return self.rev_parse("HEAD")

    def cleanup_merge_state(self) -> None:
        self.git_no_hooks("merge", "--abort", check=False)
        self.git_no_hooks("reset", "--hard", "HEAD")

    def assert_linked_worktree_branch_creation_guard(self) -> None:
        main_branch = "forbidden/from-main-worktree"
        linked_branch = "forbidden/from-linked-worktree"

        self.expect_rejected(["branch", main_branch, "HEAD"], "BRANCH_NAME_NOT_ALLOWED")

        linked_repo = self.work_root.parent / f"{self.name}-linked-worktree"
        if linked_repo.exists():
            self.git_no_hooks("worktree", "remove", "--force", str(linked_repo), check=False)
            shutil.rmtree(linked_repo, ignore_errors=True)
        self.git_no_hooks("worktree", "prune", check=False)
        self.git("worktree", "add", "--detach", str(linked_repo), "HEAD")
        install_policy_hook(linked_repo, self.config_dir, scope="worktree")

        before = self.ref_snapshot()
        result = git_raw(linked_repo, "branch", linked_branch, "HEAD", check=False)
        after = self.ref_snapshot()
        if result.returncode == 0:
            raise AssertionError(f"{self.name}: linked worktree branch creation was accepted")

        combined = result.stdout + result.stderr
        if "WORKTREE_BRANCH_CREATION_NOT_ALLOWED" not in combined:
            raise AssertionError(
                f"{self.name}: expected linked worktree branch creation rejection\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if "blocked only in this linked worktree" not in combined:
            raise AssertionError(
                f"{self.name}: expected linked worktree rejection guidance\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        expected_policy_hint = f"see policy: {(linked_repo / '.git-guard' / 'contribution.md').resolve()}"
        if expected_policy_hint not in combined:
            raise AssertionError(
                f"{self.name}: expected linked worktree policy hint {expected_policy_hint!r}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if EXPECTED_AGENT_HINT not in combined:
            raise AssertionError(
                f"{self.name}: expected linked worktree rejection to include agent guidance\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if after.refs != before.refs:
            raise AssertionError(
                f"{self.name}: rejected linked worktree branch creation changed refs\n"
                f"before: {json.dumps(before.refs, indent=2, sort_keys=True)}\n"
                f"after: {json.dumps(after.refs, indent=2, sort_keys=True)}"
            )
        if ref_exists(linked_repo, f"refs/heads/{linked_branch}"):
            raise AssertionError(f"{self.name}: linked worktree branch was created: {linked_branch}")

        disabled_config = {
            "pre_push": {
                "auto_push_missing_tags": True,
            },
            "submodules": {
                "main_guard": False,
            },
            "worktree": {
                "reject_branch_creation_in_linked_worktree": False,
            },
        }
        (linked_repo / ".git-guard" / "config.json").write_text(
            json.dumps(disabled_config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        disabled_branch = "forbidden/from-linked-worktree-disabled"
        result = git_raw(linked_repo, "branch", disabled_branch, "HEAD", check=False)
        combined = result.stdout + result.stderr
        if "WORKTREE_BRANCH_CREATION_NOT_ALLOWED" in combined or "BRANCH_NAME_NOT_ALLOWED" not in combined:
            raise AssertionError(
                f"{self.name}: expected config-disabled linked worktree creation to fall through to branch policy\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if ref_exists(linked_repo, f"refs/heads/{disabled_branch}"):
            raise AssertionError(f"{self.name}: config-disabled invalid branch was created: {disabled_branch}")

    def assert_submodule_main_guard(self) -> None:
        target_branch = self.prepare_submodule_main_guard_branch()
        self.git("checkout", target_branch)
        submodule_remote = self.create_submodule_remote()
        self.git("-c", "protocol.file.allow=always", "submodule", "add", str(submodule_remote), "modules/lib")
        result = self.git("commit", "-m", "add submodule at origin main tip")
        self.assert_output_not_contains(result, "SUBMODULE_")

        submodule = self.repo / "modules" / "lib"
        origin_base = self.git_in(submodule, "rev-parse", "--verify", "origin/main~1").stdout.strip()
        self.git_in(submodule, "checkout", "--detach", origin_base)
        self.git("add", "modules/lib")
        result = self.git("commit", "-m", "pin submodule behind origin main")
        self.assert_output_contains(result, "SUBMODULE_BEHIND_ORIGIN_MAIN")

        self.git_in(submodule, "checkout", "main")
        self.write_repo_file(submodule, "local-main.txt", "local main only\n")
        self.git_in(submodule, "add", "local-main.txt")
        self.git_in(submodule, "commit", "-m", "local main only")
        self.git("add", "modules/lib")
        result = self.git("commit", "-m", "pin submodule to local main")
        self.assert_output_contains(result, "SUBMODULE_NOT_ON_ORIGIN_MAIN_BUT_ON_LOCAL_MAIN")

        case_base = self.git_in(submodule, "rev-parse", "--verify", "origin/case/allowed/topic~1").stdout.strip()
        self.git_in(submodule, "checkout", "--detach", case_base)
        self.git("add", "modules/lib")
        result = self.git("commit", "-m", "pin submodule behind allowed case branch")
        self.assert_output_contains(result, "SUBMODULE_BEHIND_ALLOWED_REMOTE_BRANCH")
        self.assert_output_contains(result, "branch=case/allowed/topic")

        self.git_in(submodule, "checkout", "-b", "case/local/topic", "origin/case/allowed/topic")
        self.write_repo_file(submodule, "local-case.txt", "local case only\n")
        self.git_in(submodule, "add", "local-case.txt")
        self.git_in(submodule, "commit", "-m", "local case only")
        self.git("add", "modules/lib")
        result = self.git("commit", "-m", "pin submodule to local case branch")
        self.assert_output_contains(result, "SUBMODULE_NOT_ON_ALLOWED_REMOTE_BRANCH_BUT_ON_LOCAL_BRANCH")
        self.assert_output_contains(result, "branch=case/local/topic")

        allowed_parent = self.rev_parse(target_branch)
        self.git_in(submodule, "checkout", "-b", "side", "origin/main")
        self.write_repo_file(submodule, "side.txt", "side only\n")
        self.git_in(submodule, "add", "side.txt")
        self.git_in(submodule, "commit", "-m", "side only")
        self.git("add", "modules/lib")
        self.expect_rejected(
            ["commit", "-m", "pin submodule to side branch"],
            "SUBMODULE_COMMIT_NOT_ALLOWED",
            cleanup=lambda: self.reset_parent_and_submodule(allowed_parent, submodule),
        )

        self.set_submodule_main_guard(False)
        self.git_in(submodule, "checkout", "side")
        self.git("add", "modules/lib")
        result = self.git("commit", "-m", "submodule guard disabled allows side")
        self.assert_output_not_contains(result, "SUBMODULE_COMMIT_NOT_ALLOWED")
        self.git_no_hooks("reset", "--hard", allowed_parent)
        self.set_submodule_main_guard(True)
        self.git_in(submodule, "checkout", "main")

        self.set_submodule_main_guard_legacy_shape()
        self.expect_rejected(
            ["commit", "--allow-empty", "-m", "legacy submodule main guard shape is invalid"],
            "CONFIG_INVALID",
        )
        self.set_submodule_main_guard(True)
        self.set_submodule_allowed_branches("main")
        self.expect_rejected(
            ["commit", "--allow-empty", "-m", "submodule allowed branches scalar is invalid"],
            "CONFIG_INVALID",
        )
        self.set_submodule_allowed_branches(["main", "case/*/*"])

        shutil.rmtree(submodule)
        self.write_file("missing-submodule-check.txt", "missing submodule check\n")
        self.git("add", "missing-submodule-check.txt")
        self.expect_rejected(
            ["commit", "-m", "missing submodule is rejected"],
            "SUBMODULE_REPO_MISSING",
            cleanup=lambda: self.reset_parent_and_init_submodule(allowed_parent),
        )

    def prepare_submodule_main_guard_branch(self) -> str:
        preferred_names = ["feat/*", "infra/*", "release/*", "hotfix/*"]
        direct_names = [item["name"] for item in self.policy.get("direct_commit_refs", [])]
        ordered_names = [name for name in preferred_names if name in direct_names]
        ordered_names.extend(name for name in direct_names if name not in ordered_names and name != "main")

        for name in ordered_names:
            if "*" in name:
                edge = next((item for item in self.policy.get("branch_from", []) if item.get("target") == name), None)
                if not edge:
                    continue
                branch = name.replace("*", "submodule-main-guard")
                self.create_branch(branch, edge["source"])
                return branch

            self.git("checkout", name)
            return name

        raise AssertionError(f"{self.name}: cannot find a direct-commit branch for submodule main guard tests")

    def create_submodule_remote(self) -> Path:
        remote = self.work_root.parent / f"{self.name}-submodule-remote"
        if remote.exists():
            shutil.rmtree(remote)
        remote.mkdir(parents=True)
        self.git_in(remote, "init", "-b", "main")
        self.git_in(remote, "config", "user.name", "Policy Hook Test")
        self.git_in(remote, "config", "user.email", "policy-hook-test@example.invalid")
        self.write_repo_file(remote, "lib.txt", "base\n")
        self.git_in(remote, "add", "lib.txt")
        self.git_in(remote, "commit", "-m", "submodule base")
        self.write_repo_file(remote, "lib.txt", "tip\n")
        self.git_in(remote, "add", "lib.txt")
        self.git_in(remote, "commit", "-m", "submodule tip")
        self.git_in(remote, "checkout", "-b", "case/allowed/topic", "main")
        self.write_repo_file(remote, "case.txt", "case base\n")
        self.git_in(remote, "add", "case.txt")
        self.git_in(remote, "commit", "-m", "submodule case base")
        self.write_repo_file(remote, "case.txt", "case tip\n")
        self.git_in(remote, "add", "case.txt")
        self.git_in(remote, "commit", "-m", "submodule case tip")
        self.git_in(remote, "checkout", "main")
        return remote

    def set_submodule_allowed_branches(self, allowed_branches: object) -> None:
        config_path = self.repo / ".git-guard" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("submodules", {})["allowed_branches"] = allowed_branches
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def set_submodule_main_guard(self, enabled: bool) -> None:
        config_path = self.repo / ".git-guard" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("submodules", {})["main_guard"] = enabled
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def set_submodule_main_guard_legacy_shape(self) -> None:
        config_path = self.repo / ".git-guard" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("submodules", {})["main_guard"] = {"enabled": True}
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def reset_parent_and_submodule(self, parent_ref: str, submodule: Path) -> None:
        self.git_no_hooks("reset", "--hard", parent_ref)
        if submodule.exists():
            self.git_in(submodule, "checkout", "main", check=False)

    def reset_parent_and_init_submodule(self, parent_ref: str) -> None:
        self.git_no_hooks("reset", "--hard", parent_ref)
        self.git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "modules/lib")

    def write_repo_file(self, repo: Path, filename: str, content: str) -> None:
        path = repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def git_in(self, repo: Path, *args: str, check: bool = True) -> CommandResult:
        return git_raw(repo, *args, check=check)

    def assert_output_contains(self, result: CommandResult, expected: str) -> None:
        combined = result.stdout + result.stderr
        if expected not in combined:
            raise AssertionError(
                f"{self.name}: expected command output to contain {expected!r}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

    def assert_output_not_contains(self, result: CommandResult, unexpected: str) -> None:
        combined = result.stdout + result.stderr
        if unexpected in combined:
            raise AssertionError(
                f"{self.name}: command output should not contain {unexpected!r}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

    def expected_policy_hint_path(self) -> str:
        return str((self.repo / ".git-guard" / "contribution.md").resolve())

    def write_file(self, filename: str, content: str) -> None:
        path = self.repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def seed_default_branch_logs_before_install(self) -> None:
        current = self.git_no_hooks("branch", "--show-current").stdout.strip()
        for branch in self.policy.get("branches", {}).get("long_lived", []):
            if not ref_exists(self.repo, f"refs/heads/{branch}"):
                continue
            self.git_no_hooks("checkout", branch)
            self.ensure_current_branch_log_staged()
            if self.git_no_hooks("diff", "--cached", "--quiet", check=False).returncode != 0:
                self.git_no_hooks("commit", "-m", f"seed {branch} branch log")
        if current:
            self.git_no_hooks("checkout", current)

    def ensure_current_branch_log_staged(self) -> None:
        branch = self.git_no_hooks("branch", "--show-current").stdout.strip()
        if not branch:
            return
        filename = f".branch_logs/{branch_log_slug(branch)}.md"
        path = self.repo / filename
        if path.exists():
            return
        self.write_file(filename, f"# {branch}\n\nbranch-local test log\n")
        self.git_no_hooks("add", filename)

    def conflicted_paths(self) -> list[str]:
        output = self.git_no_hooks("diff", "--name-only", "--diff-filter=U").stdout
        return [line for line in output.splitlines() if line]

    def restore_target_branch_log_tree(self, target_old: str = "HEAD") -> None:
        path = ".branch_logs"
        target_had_branch_logs = self.git_no_hooks("cat-file", "-e", f"{target_old}:{path}", check=False).returncode == 0
        self.git_no_hooks("rm", "-r", "--cached", "--ignore-unmatch", path)
        branch_log_path = self.repo / path
        if branch_log_path.is_dir():
            shutil.rmtree(branch_log_path)
        elif branch_log_path.exists():
            branch_log_path.unlink()
        if target_had_branch_logs:
            self.git_no_hooks("checkout", target_old, "--", path)
            self.git_no_hooks("add", path)

    def create_branch(self, branch: str, source: str) -> None:
        self.git("checkout", source)
        self.git("checkout", "-b", branch)

    def commit_file(self, branch: str, filename: str, content: str, message: str) -> str:
        self.git("checkout", branch)
        self.write_file(filename, content)
        self.git("add", filename)
        if not filename == ".branch_logs" and not filename.startswith(".branch_logs/"):
            self.ensure_current_branch_log_staged()
        self.git("commit", "-m", message)
        return self.rev_parse("HEAD")

    def merge_to(self, source: str, target: str, message: str | None = None) -> None:
        self.git("checkout", target)
        target_old = self.rev_parse("HEAD")
        result = self.git("merge", "--no-ff", "--no-edit", "--no-commit", "-X", "ours", source, check=False)
        if result.returncode != 0:
            conflicts = self.conflicted_paths()
            non_branch_log_conflicts = [path for path in conflicts if path != ".branch_logs" and not path.startswith(".branch_logs/")]
            if not conflicts or non_branch_log_conflicts:
                raise AssertionError(
                    f"git merge --no-ff --no-edit --no-commit -X ours {source} failed in {self.repo}\n"
                    f"non-branch-log conflicts: {non_branch_log_conflicts}\n"
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )
        self.restore_target_branch_log_tree(target_old)
        remaining_conflicts = self.conflicted_paths()
        if remaining_conflicts:
            raise AssertionError(f"{self.name}: unresolved merge conflicts after branch log restore: {remaining_conflicts}")
        self.git("commit", "-m", message or f"MR {source} to {target}")

    def tag(self, name: str, ref: str) -> None:
        self.git("tag", name, ref)

    def assert_is_ancestor(self, ancestor: str, descendant: str) -> None:
        if self.git("merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode != 0:
            raise AssertionError(f"{ancestor} is not an ancestor of {descendant}")

    def rev_parse(self, ref: str) -> str:
        return self.git("rev-parse", "--verify", ref).stdout.strip()

    def ref_snapshot(self) -> RefSnapshot:
        output = self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags").stdout
        refs: dict[str, str] = {}
        for line in output.splitlines():
            ref, object_name = line.split(" ", 1)
            refs[ref] = object_name
        return RefSnapshot(refs=refs)

    def git(self, *args: str, check: bool = True) -> CommandResult:
        return git_raw(self.repo, *args, check=check)

    def git_no_hooks(self, *args: str, check: bool = True) -> CommandResult:
        return git_raw(self.repo, "-c", "core.hooksPath=.git/hooks", *args, check=check)

    def run_command(self, args: list[str], check: bool = True) -> CommandResult:
        return run_raw(self.repo, args, check=check)


def git_raw(cwd: Path, *args: str, check: bool = True) -> CommandResult:
    return run_raw(cwd, ["git", *args], check=check)


def ref_exists(cwd: Path, ref: str) -> bool:
    return git_raw(cwd, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def branch_log_slug(branch: str) -> str:
    return branch.replace("/", "__").replace("@", "_")


def run_raw(cwd: Path, args: list[str], check: bool = True) -> CommandResult:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Policy Hook Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "policy-hook-test@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Policy Hook Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "policy-hook-test@example.invalid")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") if not existing_pythonpath else f"{PROJECT_ROOT / 'src'}{os.pathsep}{existing_pythonpath}"
    env.setdefault("GIT_GUARD_BIN", "python3 -m cli")
    result = subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    wrapped = CommandResult(result.returncode, result.stdout, result.stderr)
    if check and result.returncode != 0:
        raise AssertionError(
            f"{' '.join(args)} failed in {cwd}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return wrapped
