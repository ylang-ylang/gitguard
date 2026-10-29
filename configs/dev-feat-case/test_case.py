from __future__ import annotations

import json
import shutil
from typing import Any

from configs.test_base import PolicyHookTestBase, ref_exists, run_raw


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
        self.expect_rejected(["branch", "case/missing-topic", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_rejected(["branch", "case/from-feature/bad", "feat/unmerged"], "BRANCH_SOURCE_MISMATCH")
        self.expect_rejected(["branch", "wrong/test", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_rejected(["branch", "release/demo", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_illegal_branch_rename_rejected()
        self.assert_same_sha_tie_merge_allowed()

        self.git("checkout", "dev")
        self.expect_merge_rejected("case/dev-context/reject-to-dev", "PROTECTED_REF_NO_ALLOWED_SOURCE")

        self.git("checkout", "main")
        self.expect_merge_rejected("case/dev-context/reject-to-dev", "PROTECTED_REF_NO_ALLOWED_SOURCE")

        self.git("checkout", "dev")
        self.ensure_current_branch_log_staged()
        self.expect_rejected(
            ["commit", "--allow-empty", "-m", "direct dev commit"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
        )
        self.git_no_hooks("reset", "--hard", "HEAD")
        self.assert_protected_branch_guard_can_be_disabled()

        self.git("checkout", "main")
        self.ensure_current_branch_log_staged()
        self.expect_rejected(
            ["commit", "--allow-empty", "-m", "direct main commit"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
        )
        self.git_no_hooks("reset", "--hard", "HEAD")
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
        self.assert_annotated_release_tag_flow()
        self.assert_pre_push_auto_syncs_release_tags()
        self.assert_pre_push_auto_sync_can_be_disabled()
        self.assert_branch_log_pre_commit_guards()
        self.assert_per_branch_log_merge_guards()

    def assert_same_sha_tie_merge_allowed(self) -> None:
        # A rule-less feat/* branch that shares dev's current SHA must not poison a
        # dev -> feat/* merge: introduced heads are grouped by SHA, and a group is
        # accepted when any ref in it matches a merge rule for the update target.
        tie_idle = "feat/tie-idle"
        tie_work = "feat/tie-work"
        self.create_branch(tie_idle, "dev")
        self.create_branch(tie_work, "dev")
        tie_work_old = self.commit_file(tie_work, "tie-work.txt", "tie work\n", "tie work commit")

        advance = "feat/tie-advance"
        self.create_branch(advance, "dev")
        self.commit_file(advance, "tie-advance.txt", "tie advance\n", "advance dev through feature")
        self.merge_to(advance, "dev")
        self.git("branch", "-D", advance)
        dev_tip = self.rev_parse("dev")

        # Re-point the idle branch so it shares dev's CURRENT sha: branch creation
        # requires the exact dev tip, so delete and recreate it.
        self.git("branch", "-D", tie_idle)
        self.create_branch(tie_idle, "dev")
        if self.rev_parse(tie_idle) != dev_tip:
            raise AssertionError(f"{self.name}: feat/tie-idle should share dev's current tip {dev_tip}")

        # Merging dev into feat/tie-work must succeed even though feat/tie-idle is
        # an idle rule-less branch tied at dev's exact SHA.
        self.merge_to("dev", tie_work)
        merge_sha = self.rev_parse(tie_work)
        if merge_sha == tie_work_old:
            raise AssertionError(f"{self.name}: dev -> {tie_work} merge did not create a merge commit")
        parents = self.git("rev-list", "--parents", "-n", "1", tie_work).stdout.split()
        if len(parents) != 3:
            raise AssertionError(f"{self.name}: expected a two-parent merge commit, got {parents}")
        self.assert_is_ancestor(dev_tip, tie_work)

        # A rule-less feat/* branch with a unique commit must still be rejected when
        # merged into another feat/* branch (no feat/* -> feat/* rule exists).
        no_rule = "feat/no-rule"
        self.create_branch(no_rule, "dev")
        self.commit_file(no_rule, "no-rule.txt", "no rule work\n", "no rule feature commit")
        self.git("checkout", tie_work)
        result = self.expect_merge_rejected(no_rule, "MANAGED_BRANCH_SOURCE_NOT_ALLOWED")
        combined = result.stdout + result.stderr
        if f"source_refs=refs/heads/{no_rule}" not in combined:
            raise AssertionError(
                f"{self.name}: expected rejection to list source_refs=refs/heads/{no_rule}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

    def checkout_final_branch(self) -> None:
        self.git("checkout", "dev")

    def expect_illegal_branch_rename_rejected(self) -> None:
        branch = "feat/rename-guard"
        self.create_branch(branch, "dev")
        self.expect_rejected(["branch", "-m", branch, "wrong/renamed"], "BRANCH_NAME_NOT_ALLOWED")

        self.git("checkout", "dev")
        self.expect_rejected(["branch", "-m", branch, "wrong/renamed-noncurrent"], "BRANCH_NAME_NOT_ALLOWED")

        self.git("branch", "-m", branch, "feat/renamed-ok")
        if not ref_exists(self.repo, "refs/heads/feat/renamed-ok"):
            raise AssertionError(f"{self.name}: rename to a policy-allowed name was rejected")
        self.git("branch", "-D", "feat/renamed-ok")

        self.git_no_hooks("branch", "wrong/legacy", "dev")
        self.git("branch", "-D", "wrong/legacy")

    def create_feature_release(self) -> None:
        if self.rev_parse("main") == self.rev_parse("dev"):
            raise AssertionError(f"{self.name}: case origin tests require distinct main and dev tips")
        main_case_branch = "case/main-context/bootstrap"
        self.create_branch(main_case_branch, "main")
        main_case_sha = self.commit_file(main_case_branch, "case-main.txt", "case from main\n", "case work from main")
        self.assert_branch_base_source(main_case_branch, "refs/heads/main")

        dev_case_branch = "case/dev-context/bootstrap"
        self.create_branch(dev_case_branch, "dev")
        self.assert_branch_base_source(dev_case_branch, "refs/heads/dev")
        dev_case_sha = self.commit_file(dev_case_branch, "case-dev.txt", "case from dev\n", "case work from dev")

        branch = "feat/initial"
        self.create_branch(branch, "dev")
        self.merge_to(main_case_branch, branch)
        self.merge_to(dev_case_branch, branch)
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
        self.assert_is_ancestor(dev_case_sha, branch)
        self.assert_is_ancestor(dev_case_sha, "dev")
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
        self.create_branch(branch, "dev")
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
        self.expect_merge_rejected("dev", "PENDING_TAG_TARGET_MOVED")

    def state(self) -> dict[str, Any]:
        return super().state()

    def assert_pending_tag_count(self, expected: int) -> None:
        pending_tags = self.state().get("pending_tags", {})
        if len(pending_tags) != expected:
            raise AssertionError(f"{self.name}: expected {expected} pending tags, got {json.dumps(pending_tags, indent=2, sort_keys=True)}")

    def assert_annotated_release_tag_flow(self) -> None:
        # Annotated tag objects must be peeled to the target commit inside the
        # reference-transaction quarantine: the tag object must validate against
        # the merge result, and creating it must clear the pending tag lock.
        self.merge_to("dev", "main")
        annotated_main_sha = self.rev_parse("main")
        self.assert_pending_tag_count(1)
        result = self.git("tag", "-a", "V1.2", "-m", "annotated release", annotated_main_sha)
        if result.returncode != 0:
            raise AssertionError(
                f"{self.name}: annotated release tag was rejected\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        self.assert_pending_tag_count(0)
        peeled = self.git("rev-parse", "--verify", "V1.2^{}").stdout.strip()
        if peeled != annotated_main_sha:
            raise AssertionError(f"{self.name}: annotated tag should peel to {annotated_main_sha}, got {peeled}")

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

        run_raw(self.work_root.parent, ["git", "--git-dir", str(remote), "update-ref", "refs/tags/V1.1", self.rev_parse("V1.0")])
        result = self.git_with_env({"GG_INTERNAL_TAG_SYNC": "1"}, "push", "origin", "dev:dev", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "PUSH_TAG_CONFLICT" not in combined:
            raise AssertionError(
                f"{self.name}: GG_INTERNAL_TAG_SYNC env should not bypass pre-push tag conflict checks\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

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
        owner_log = self.branch_log_path_for_branch(branch)
        self.ensure_current_branch_log_staged()
        self.write_file(".branch_logs/branches/untracked.md", "untracked branch log\n")
        self.write_file("log-tracking.txt", "work\n")
        self.git("add", "log-tracking.txt")
        result = self.git("commit", "-m", "missing staged branch log", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "BRANCH_LOG_UNTRACKED" not in combined:
            raise AssertionError(
                f"{self.name}: expected untracked branch log rejection\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        self.git("add", ".branch_logs/branches/untracked.md")
        self.git("commit", "-m", "track branch log")
        tracked_branch_logs = self.git("ls-tree", "-r", "--name-only", "HEAD", "--", ".branch_logs").stdout.splitlines()
        if owner_log not in tracked_branch_logs or ".branch_logs/branches/untracked.md" not in tracked_branch_logs:
            raise AssertionError(f"{self.name}: branch log commit should include real branch log file: {tracked_branch_logs}")

        config_path = self.repo / ".git-guard" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        required_branch = "feat/log-required"
        self.create_branch(required_branch, "dev")
        self.write_file("log-required.txt", "work\n")
        self.git("add", "log-required.txt")
        result = self.git("commit", "-m", "missing required branch log", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "BRANCH_LOG_CHANGE_REQUIRED" not in combined:
            raise AssertionError(
                f"{self.name}: expected required branch log change rejection\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        required_log = self.branch_log_path_for_branch(required_branch)
        self.write_file(required_log, self.branch_log_template(required_branch) + "- required branch log\n")
        self.git("add", required_log)
        self.git("commit", "-m", "add required branch log")

        config.setdefault("branch_logs", {})["path"] = ".branch-log.md"
        config.setdefault("branch_logs", {})["force_diff_required"] = True
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        required_file_branch = "feat/log-required-file"
        self.create_branch(required_file_branch, "dev")
        self.write_file("log-required-file.txt", "work\n")
        self.git("add", "log-required-file.txt")
        result = self.git("commit", "-m", "missing required branch log file", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "CONFIG_INVALID" not in combined:
            raise AssertionError(
                f"{self.name}: expected invalid branch log file path rejection\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        config.setdefault("branch_logs", {})["path"] = ".branch_logs/"
        config.setdefault("branch_logs", {})["force_diff_required"] = True
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.git_no_hooks("reset", "--hard", "HEAD")

    def assert_per_branch_log_merge_guards(self) -> None:
        branch = "feat/log-merge"
        self.create_branch(branch, "dev")
        self.git("checkout", branch)
        feature_log = self.branch_log_path_for_branch(branch)
        self.write_file("log-merge-feature.txt", "feature\n")
        self.ensure_current_branch_log_staged()
        self.git("add", "log-merge-feature.txt")
        self.git("commit", "-m", "feature with branch log")

        self.git("checkout", "dev")
        self.git("merge", "--no-ff", "--no-edit", "--no-commit", branch)
        result = self.git("commit", "-m", f"MR {branch} to dev without dev log", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "BRANCH_LOG_CHANGE_REQUIRED" not in combined:
            raise AssertionError(
                f"{self.name}: expected merge commit without target branch log to be rejected\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        self.ensure_current_branch_log_staged()
        self.git("commit", "-m", f"MR {branch} to dev with dev log")
        if not (self.repo / feature_log).exists():
            raise AssertionError(f"{self.name}: source branch log should be preserved in dev: {feature_log}")

        manual_branch = "feat/log-manual"
        self.create_branch(manual_branch, "dev")
        self.commit_file(manual_branch, "log-manual-feature.txt", "feature\n", "manual feature with branch log")
        manual_log = self.branch_log_path_for_branch(manual_branch)
        manual_log_sha = self.rev_parse(manual_branch)
        self.git("checkout", "dev")
        self.merge_to(manual_branch, "dev")

        if not (self.repo / manual_log).exists():
            raise AssertionError(f"{self.name}: source branch log should be kept on target: {manual_log}")
        if not self.rev_parse("dev"):
            raise AssertionError(f"{self.name}: dev should move after merge of {manual_branch} at {manual_log_sha}")

        self.assert_per_branch_log_sync_merge()

    def assert_per_branch_log_sync_merge(self) -> None:
        clean_dev_sha = self.rev_parse("dev")
        branch = "feat/log-sync-target"
        self.create_branch(branch, "dev")
        self.commit_file(branch, "log-sync-target.txt", "feature work\n", "feature work before sync")
        target_log_sha = self.rev_parse(branch)

        self.git("checkout", "dev")
        self.ensure_current_branch_log_staged()
        self.write_file("dev-source.txt", "source branch work\n")
        self.git_no_hooks("add", "dev-source.txt", self.branch_log_path_for_branch("dev"))
        self.git_no_hooks("commit", "-m", "seed dev branch log with hooks disabled")

        self.git("checkout", branch)
        self.git("merge", "--no-ff", "--no-edit", "--no-commit", "dev")
        result = self.git("commit", "-m", "sync dev without branch log", check=False)
        combined = result.stdout + result.stderr
        if result.returncode == 0 or "BRANCH_LOG_CHANGE_REQUIRED" not in combined:
            raise AssertionError(
                f"{self.name}: expected sync merge without feature branch log to be rejected\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        self.ensure_current_branch_log_staged()
        self.git("commit", "-m", "sync dev with feature branch log")
        if self.rev_parse(branch) == target_log_sha:
            raise AssertionError(f"{self.name}: sync merge did not move feature branch")
        if not (self.repo / self.branch_log_path_for_branch("dev")).exists():
            raise AssertionError(f"{self.name}: source dev branch log should be preserved during sync merge")

        self.git_no_hooks("branch", "-f", "dev", clean_dev_sha)


    def assert_protected_branch_guard_can_be_disabled(self) -> None:
        self.git("checkout", "dev")
        clean_dev_sha = self.rev_parse("dev")
        config_path = self.repo / ".git-guard" / "config.json"
        original_config = config_path.read_text(encoding="utf-8")
        try:
            config = json.loads(original_config)
            config.setdefault("protected_branches", {})["enabled"] = False
            config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            self.write_file("direct-dev-emergency.txt", "emergency change\n")
            self.git("add", "direct-dev-emergency.txt")
            self.ensure_current_branch_log_staged()
            result = self.git("commit", "-m", "emergency direct dev commit")
            combined = result.stdout + result.stderr
            if "PROTECTED_BRANCH_GUARD_DISABLED" not in combined:
                raise AssertionError(
                    f"{self.name}: expected disabled protected branch guard warning\n"
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )
        finally:
            config_path.write_text(original_config, encoding="utf-8")
            self.git_no_hooks("reset", "--hard", clean_dev_sha)


TEST_CASE = DevFeatCaseHookTest
