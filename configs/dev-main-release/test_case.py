from __future__ import annotations

import json
from typing import Any

from configs.test_base import PolicyHookTestBase


START_SYMBOL = "=========== GIT FLOW GUARD REJECTION TESTS START ==========="


class DevMainReleaseHookTest(PolicyHookTestBase):
    config_name = "dev-main-release"

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
        self.merge_to("dev", "main")
        self.create_completed_release_flow()

    def create_rejection_test_fixtures(self) -> None:
        pass

    def mark_rejection_tests_start(self) -> None:
        self.git("checkout", "dev")
        self.write_file("TEST_START.txt", START_SYMBOL + "\n")
        self.git("add", "TEST_START.txt")
        self.git("commit", "-m", START_SYMBOL)

    def run_rejection_tests(self) -> None:
        self.create_pending_release_flow()
        self.assert_pending_tag_count(1)

        self.create_allowed_dev_merge_while_release_tag_pending()
        self.assert_pending_tag_count(1)

        self.expect_pending_release_source_move_rejected()
        self.assert_pending_tag_count(1)

        self.create_blocked_release_flow()
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", "release/1.2"],
            "PENDING_TAG_REQUIRED",
            cleanup=self.cleanup_merge_state,
        )

        self.tag("V1.1", self.pending_release_sha)
        self.assert_pending_tag_count(0)

        self.merge_to("release/1.2", "main")
        self.tag("V1.2", self.blocked_release_sha)
        self.assert_pending_tag_count(0)

    def checkout_final_branch(self) -> None:
        self.git("checkout", "dev")

    def create_completed_release_flow(self) -> None:
        branch = "release/1.0"
        self.create_branch(branch, "dev")
        self.release_sha = self.commit_file(branch, "release-1.0.txt", "release 1.0\n", "release 1.0")
        self.merge_to(branch, "main")
        self.tag("V1.0", self.release_sha)
        self.assert_is_ancestor(self.release_sha, "main")
        self.assert_pending_tag_count(0)

    def create_pending_release_flow(self) -> None:
        branch = "release/1.1"
        self.create_branch(branch, "dev")
        self.pending_release_sha = self.commit_file(branch, "release-1.1.txt", "release 1.1\n", "release 1.1")
        self.merge_to(branch, "main")
        self.assert_is_ancestor(self.pending_release_sha, "main")

    def create_allowed_dev_merge_while_release_tag_pending(self) -> None:
        self.dev_pending_sha = self.commit_file("dev", "dev-pending.txt", "dev while release tag pending\n", "dev while release tag pending")
        self.merge_to("dev", "main")
        self.assert_is_ancestor(self.dev_pending_sha, "main")

    def expect_pending_release_source_move_rejected(self) -> None:
        self.git("checkout", "release/1.1")
        self.write_file("release-1.1-move.txt", "move pending release\n")
        self.git("add", "release-1.1-move.txt")
        self.expect_rejected(
            ["commit", "-m", "move pending release"],
            "PENDING_TAG_SOURCE_MOVED",
            cleanup=self.cleanup_merge_state,
        )

    def create_blocked_release_flow(self) -> None:
        branch = "release/1.2"
        self.create_branch(branch, "dev")
        self.blocked_release_sha = self.commit_file(branch, "release-1.2.txt", "release 1.2\n", "release 1.2")
        self.git("checkout", "main")

    def state(self) -> dict[str, Any]:
        return json.loads((self.repo / ".git" / "git-flow-guard-state.json").read_text(encoding="utf-8"))

    def assert_pending_tag_count(self, expected: int) -> None:
        pending_tags = self.state().get("pending_tags", {})
        if len(pending_tags) != expected:
            raise AssertionError(f"{self.name}: expected {expected} pending tags, got {json.dumps(pending_tags, indent=2, sort_keys=True)}")


TEST_CASE = DevMainReleaseHookTest
