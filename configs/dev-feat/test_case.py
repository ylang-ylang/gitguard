from __future__ import annotations

import json
import shutil
from typing import Any

from configs.test_base import PolicyHookTestBase, run_raw


START_SYMBOL = "=========== GIT GUARD REJECTION TESTS START ==========="


class DevFeatHookTest(PolicyHookTestBase):
    config_name = "dev-feat"

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

    def mark_rejection_tests_start(self) -> None:
        branch = "feat/test-start"
        self.create_branch(branch, "dev")
        self.start_marker_sha = self.commit_file(branch, "TEST_START.txt", START_SYMBOL + "\n", START_SYMBOL)
        self.merge_to(branch, "dev", message=START_SYMBOL)
        self.marker_dev_sha = self.rev_parse("dev")

    def run_rejection_tests(self) -> None:
        self.expect_rejected(["branch", "feat/from-main", "main"], "BRANCH_SOURCE_MISMATCH")
        self.expect_rejected(["branch", "release/demo", "dev"], "BRANCH_NAME_NOT_ALLOWED")

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

    def checkout_final_branch(self) -> None:
        self.git("checkout", "dev")

    def create_feature_release(self) -> None:
        branch = "feat/initial"
        self.create_branch(branch, "dev")
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
        self.git("merge", "--no-ff", "--no-edit", branch)

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


TEST_CASE = DevFeatHookTest
