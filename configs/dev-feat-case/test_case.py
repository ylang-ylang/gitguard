from __future__ import annotations

import json
import shutil
from typing import Any

from configs.test_base import PolicyHookTestBase, run_raw


START_SYMBOL = "=========== GIT GUARD REJECTION TESTS START ==========="


class DevFeatCaseHookTest(PolicyHookTestBase):
    config_name = "dev-feat-case"

    def create_initial_repo(self) -> None:
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Policy Hook Test")
        self.git("config", "user.email", "policy-hook-test@example.invalid")

        self.write_file("README.md", "initial\n")
        self.git("add", "README.md")
        self.git("commit", "-m", "initial commit")

        self.git("branch", "dev")
        self.git("checkout", "dev")
        self.write_file("dev.txt", "dev baseline\n")
        self.git("add", "dev.txt")
        self.git("commit", "-m", "dev baseline")
        self.git("checkout", "main")

    def create_correct_git_dag_tree(self) -> None:
        self.create_feature_release()

    def create_rejection_test_fixtures(self) -> None:
        self.create_unmerged_feature_fixture()
        self.create_case_reject_to_dev_fixture()

    def mark_rejection_tests_start(self) -> None:
        branch = "feat/test-start"
        self.create_branch(branch, "dev")
        self.start_marker_sha = self.commit_file(branch, "TEST_START.txt", START_SYMBOL + "\n", START_SYMBOL)
        self.merge_to(branch, "dev", message=START_SYMBOL)
        self.marker_dev_sha = self.rev_parse("dev")

    def run_rejection_tests(self) -> None:
        self.expect_rejected(["branch", "feat/from-main", "main"], "BRANCH_SOURCE_MISMATCH")
        self.expect_rejected(["branch", "case/from-dev/bad", "dev"], "BRANCH_SOURCE_MISMATCH")
        self.expect_rejected(["branch", "case/missing-topic", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_rejected(["branch", "case/from-feature/bad", "feat/unmerged"], "BRANCH_SOURCE_MISMATCH")
        self.expect_rejected(["branch", "wrong/test", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_rejected(["branch", "release/demo", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_illegal_branch_rename_rejected()

        self.git("checkout", "dev")
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", "case/dev-context/reject-to-dev"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
            cleanup=self.cleanup_merge_state,
        )

        self.git("checkout", "dev")
        self.expect_rejected(
            ["commit", "--allow-empty", "-m", "direct dev commit"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
        )

        self.git("checkout", "main")
        self.expect_rejected(
            ["commit", "--allow-empty", "-m", "direct main commit"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
        )
        self.expect_rejected(["tag", "V2.0", "dev"], "TAG_TARGET_NOT_TARGET_HEAD")

        self.expect_rejected(["tag", "v1.1", "main"], "TAG_TARGET_TAG_PATTERN_MISMATCH")
        self.expect_rejected(["tag", "V1.1.0", "main"], "TAG_TARGET_TAG_PATTERN_MISMATCH")
        self.expect_rejected(["tag", "V1.1", self.unmerged_feature_sha], "TAG_TARGET_NOT_TARGET_HEAD")

        self.create_pending_dev_release()
        self.assert_pending_tag_count(1)
        self.expect_rejected(["tag", "V0.9", self.pending_main_sha], "TAG_VERSION_NOT_INCREMENTAL")
        self.assert_pending_dev_source_move_allowed()
        self.assert_pending_tag_count(1)
        self.expect_pending_main_target_move_rejected()
        self.assert_pending_tag_count(1)
        self.tag("V1.1", self.pending_main_sha)
        self.assert_pending_tag_count(0)
        self.assert_pre_push_auto_syncs_release_tags()
        self.assert_pre_push_auto_sync_can_be_disabled()
        self.assert_branch_log_pre_commit_guards()
        self.assert_branch_log_target_invariant_on_merge()

    def checkout_final_branch(self) -> None:
        self.git("checkout", "dev")

    def expect_illegal_branch_rename_rejected(self) -> None:
        branch = "feat/rename-guard"
        self.create_branch(branch, "dev")
        self.expect_rejected(["branch", "-m", branch, "wrong/renamed"], "BRANCH_NAME_NOT_ALLOWED")

    def create_feature_release(self) -> None:
        main_case_branch = "case/main-context/bootstrap"
        self.create_branch(main_case_branch, "main")
        main_case_sha = self.commit_file(main_case_branch, "case-main.txt", "case from main\n", "case work from main")
        self.assert_branch_base_source(main_case_branch, "refs/heads/main")

        branch = "feat/initial"
        self.create_branch(branch, "dev")
        self.merge_to(main_case_branch, branch)
        self.feature_sha = self.commit_file(branch, "feature.txt", "feature\n", "feature work")

        advance_branch = "feat/dev-advance"
        self.create_branch(advance_branch, "dev")
        dev_advance_sha = self.commit_file(advance_branch, "dev-advance.txt", "dev advance\n", "advance dev through feature")
        self.merge_to(advance_branch, "dev")

        self.merge_to("dev", branch)
        self.merge_to(branch, "dev")
        self.dev_release_sha = self.rev_parse("dev")
        self.merge_to("dev", "main")
        self.main_release_sha = self.rev_parse("main")
        self.tag("V1.0", self.main_release_sha)
        self.assert_is_ancestor(main_case_sha, branch)
        self.assert_is_ancestor(self.feature_sha, "dev")
        self.assert_is_ancestor(dev_advance_sha, branch)
        self.assert_is_ancestor(self.dev_release_sha, "main")
        self.assert_pending_tag_count(0)

    def create_unmerged_feature_fixture(self) -> None:
        branch = "feat/unmerged"
        self.create_branch(branch, "dev")
        self.unmerged_feature_sha = self.commit_file(
            branch,
            "unmerged-feature.txt",
            "unmerged feature\n",
            "fixture unmerged feature",
        )

    def create_case_reject_to_dev_fixture(self) -> None:
        branch = "case/dev-context/reject-to-dev"
        self.create_branch(branch, "main")
        self.commit_file(branch, "case-reject-dev.txt", "case reject dev\n", "fixture case reject to dev")

    def assert_branch_base_source(self, branch: str, source_ref: str) -> None:
        branch_ref = f"refs/heads/{branch}"
        branch_base = self.state().get("branch_bases", {}).get(branch_ref)
        if branch_base is None:
            raise AssertionError(f"{self.name}: expected branch base state for {branch_ref}")
        if branch_base.get("source_ref") != source_ref:
            raise AssertionError(
                f"{self.name}: expected {branch_ref} source {source_ref}, got {json.dumps(branch_base, indent=2, sort_keys=True)}"
            )

    def create_pending_dev_release(self) -> None:
        self.merge_to("dev", "main")
        self.pending_main_sha = self.rev_parse("main")
        self.assert_is_ancestor(self.marker_dev_sha, "main")

    def assert_pending_dev_source_move_allowed(self) -> None:
        branch = "feat/pending-move"
        self.create_branch(branch, "dev")
        self.pending_move_sha = self.commit_file(
            branch,
            "pending-move.txt",
            "pending move\n",
            "pending dev move",
        )
        self.git("checkout", "dev")
        self.merge_to(branch, "dev")

    def expect_pending_main_target_move_rejected(self) -> None:
        self.git("checkout", "main")
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", "dev"],
            "PENDING_TAG_TARGET_MOVED",
            cleanup=self.cleanup_merge_state,
        )

    def state(self) -> dict[str, Any]:
        return json.loads((self.repo / ".git" / "git-guard-state.json").read_text(encoding="utf-8"))

    def assert_pending_tag_count(self, expected: int) -> None:
        pending_tags = self.state().get("pending_tags", {})
        if len(pending_tags) != expected:
            raise AssertionError(f"{self.name}: expected {expected} pending tags, got {json.dumps(pending_tags, indent=2, sort_keys=True)}")

    def assert_pre_push_auto_syncs_release_tags(self) -> None:
        remote = self.work_root.parent / f"{self.name}-remote.git"
        if remote.exists():
            shutil.rmtree(remote)
        run_raw(self.work_root.parent, ["git", "init", "--bare", str(remote)])
        self.git("remote", "add", "origin", str(remote))

        result = self.git("push", "origin", "main")
        combined = result.stdout + result.stderr
        if "auto-pushing missing release tags" not in combined:
            raise AssertionError(f"{self.name}: pre-push did not announce missing release tag sync\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        for tag in ["V1.0", "V1.1"]:
            if f"auto-pushed release tag tag=refs/tags/{tag}" not in combined:
                raise AssertionError(f"{self.name}: pre-push did not announce synced tag {tag}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

        remote_tags = self.git("ls-remote", "--tags", "origin").stdout
        for tag in ["V1.0", "V1.1"]:
            expected = self.rev_parse(tag)
            expected_line = f"{expected}\trefs/tags/{tag}"
            if expected_line not in remote_tags:
                raise AssertionError(f"{self.name}: remote is missing synced tag {tag}\nexpected: {expected_line}\nremote tags:\n{remote_tags}")

    def assert_pre_push_auto_sync_can_be_disabled(self) -> None:
        config_path = self.repo / ".git-guard" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("pre_push", {})["auto_push_missing_tags"] = False
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        remote = self.work_root.parent / f"{self.name}-manual-tags-remote.git"
        if remote.exists():
            shutil.rmtree(remote)
        run_raw(self.work_root.parent, ["git", "init", "--bare", str(remote)])
        self.git("remote", "add", "manual-tags", str(remote))

        result = self.git("push", "manual-tags", "main")
        combined = result.stdout + result.stderr
        if "auto-pushing missing release tags" in combined:
            raise AssertionError(f"{self.name}: pre-push auto-sync ran even though config disabled it\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

        remote_tags = self.git("ls-remote", "--tags", "manual-tags").stdout
        for tag in ["V1.0", "V1.1"]:
            if f"refs/tags/{tag}" in remote_tags:
                raise AssertionError(f"{self.name}: remote unexpectedly received disabled auto-sync tag {tag}\nremote tags:\n{remote_tags}")

        config.setdefault("pre_push", {})["auto_push_missing_tags"] = True
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def assert_branch_log_pre_commit_guards(self) -> None:
        branch = "feat/log-tracking"
        self.create_branch(branch, "dev")
        self.write_file(".branch_logs/untracked.md", "untracked branch log\n")
        self.write_file("log-tracking.txt", "work\n")
        self.git("add", "log-tracking.txt")

        result = self.git("commit", "-m", "missing staged branch log", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "BRANCH_LOG_UNTRACKED" not in combined:
            raise AssertionError(
                f"{self.name}: expected untracked branch log rejection\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        self.git("add", ".branch_logs/untracked.md")
        self.git("commit", "-m", "track branch log")

        config_path = self.repo / ".git-guard" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        required_branch = "feat/log-required"
        self.create_branch(required_branch, "dev")
        self.git("rm", "-r", ".branch_logs")
        self.write_file("log-required.txt", "work\n")
        self.git("add", "log-required.txt")
        result = self.git("commit", "-m", "missing required branch log", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "BRANCH_LOG_REQUIRED" not in combined:
            raise AssertionError(
                f"{self.name}: expected required branch log rejection\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        self.git_no_hooks("reset", "--hard", "HEAD")

        self.write_file(".branch_logs/required.md", "required branch log\n")
        self.write_file("log-required.txt", "work\n")
        self.git("add", "log-required.txt")
        self.git("add", ".branch_logs/required.md")
        self.git("commit", "-m", "add required branch log")

        config.setdefault("branch_logs", {})["path"] = ".branch-log.md"
        config.setdefault("branch_logs", {})["force_required"] = True
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        required_file_branch = "feat/log-required-file"
        self.create_branch(required_file_branch, "dev")
        self.write_file("log-required-file.txt", "work\n")
        self.git("add", "log-required-file.txt")
        result = self.git("commit", "-m", "missing required branch log file", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "BRANCH_LOG_REQUIRED" not in combined:
            raise AssertionError(
                f"{self.name}: expected required branch log file rejection\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        self.write_file(".branch-log.md", "required branch log file\n")
        self.git("add", ".branch-log.md")
        self.git("commit", "-m", "add required branch log file")

        config.setdefault("branch_logs", {})["path"] = ".branch_logs/"
        config.setdefault("branch_logs", {})["force_required"] = True
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def assert_branch_log_target_invariant_on_merge(self) -> None:
        branch = "feat/log-drop"
        self.create_branch(branch, "dev")
        self.commit_file(branch, "log-drop-feature.txt", "feature\n", "feature with branch log")
        self.commit_file(branch, ".branch_logs/log-drop.md", "branch-local log\n", "record branch log")

        self.git("checkout", "dev")
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", branch],
            "BRANCH_LOG_TARGET_CHANGED",
            cleanup=self.cleanup_merge_state,
        )

        self.git("checkout", "dev")
        self.git("merge", "--no-ff", "--no-commit", branch)
        self.restore_target_branch_log_tree()
        self.git("commit", "-m", f"MR {branch} to dev without branch log")

        if (self.repo / ".branch_logs" / "log-drop.md").exists():
            raise AssertionError(f"{self.name}: source branch log leaked into dev")

        self.assert_branch_log_target_invariant_on_sync_merge()

    def assert_branch_log_target_invariant_on_sync_merge(self) -> None:
        clean_dev_sha = self.rev_parse("dev")
        branch = "feat/log-sync-target"
        self.create_branch(branch, "dev")
        self.commit_file(branch, "log-sync-target.txt", "feature work\n", "feature work before sync")
        target_log_sha = self.commit_file(branch, ".branch_logs/target.md", "target-local log\n", "record target branch log")

        self.git("checkout", "dev")
        self.write_file(".branch_logs/dev-source.md", "source branch log should not propagate\n")
        self.git_no_hooks("add", ".branch_logs/dev-source.md")
        self.git_no_hooks("commit", "-m", "seed source branch log with hooks disabled")

        self.git("checkout", branch)
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", "dev"],
            "BRANCH_LOG_TARGET_CHANGED",
            cleanup=self.cleanup_merge_state,
        )
        if self.rev_parse(branch) != target_log_sha:
            raise AssertionError(f"{self.name}: rejected sync merge moved target branch")

        self.git_no_hooks("branch", "-f", "dev", clean_dev_sha)


TEST_CASE = DevFeatCaseHookTest
