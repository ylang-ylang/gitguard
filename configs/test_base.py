from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar

from install import install as install_policy_hook
from mermaid import load_policy_from_markdown


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

    def install_hook(self) -> None:
        install_policy_hook(self.repo, self.config_dir, scope="worktree")
        expected_files = [
            self.repo / ".git-guard" / "contribution.md",
            self.repo / ".git-guard" / "config.json",
            self.repo / ".git-guard" / "policy.json",
            self.repo / ".git-guard" / "enable.sh",
            self.repo / ".git-guard" / "hooks" / "reference-transaction",
            self.repo / ".git-guard" / "hooks" / "pre-push",
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
        if config.get("worktree", {}).get("reject_branch_creation_in_linked_worktree") is not True:
            raise AssertionError(f"{self.name}: config should enable linked worktree branch creation guard by default")
        if config.get("pre_push", {}).get("auto_push_missing_tags") is not True:
            raise AssertionError(f"{self.name}: config should enable missing tag auto-push by default")
        self.git_no_hooks("config", "--worktree", "--unset", "core.hooksPath", check=False)
        result = self.run_command([".git-guard/enable.sh"], check=True)
        if "core.hooksPath=.git-guard/hooks" not in result.stdout:
            raise AssertionError(f"{self.name}: enable.sh did not report hook path\nstdout:\n{result.stdout}")
        hook_path = self.git_no_hooks("config", "--worktree", "--get", "core.hooksPath").stdout.strip()
        if hook_path != ".git-guard/hooks":
            raise AssertionError(f"{self.name}: enable.sh set unexpected hooksPath: {hook_path}")

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
        if "create a new worktree directory" not in combined:
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

    def expected_policy_hint_path(self) -> str:
        return str((self.repo / ".git-guard" / "contribution.md").resolve())

    def write_file(self, filename: str, content: str) -> None:
        path = self.repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def create_branch(self, branch: str, source: str) -> None:
        self.git("checkout", source)
        self.git("checkout", "-b", branch)

    def commit_file(self, branch: str, filename: str, content: str, message: str) -> str:
        self.git("checkout", branch)
        self.write_file(filename, content)
        self.git("add", filename)
        self.git("commit", "-m", message)
        return self.rev_parse("HEAD")

    def merge_to(self, source: str, target: str, message: str | None = None) -> None:
        self.git("checkout", target)
        self.git("merge", "--no-ff", "--no-edit", "-m", message or f"MR {source} to {target}", source)

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


def run_raw(cwd: Path, args: list[str], check: bool = True) -> CommandResult:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Policy Hook Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "policy-hook-test@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Policy Hook Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "policy-hook-test@example.invalid")
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
