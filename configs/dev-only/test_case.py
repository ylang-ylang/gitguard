from __future__ import annotations

import json
from typing import Any

from configs.test_base import PolicyHookTestBase


START_SYMBOL = "=========== GIT FLOW GUARD REJECTION TESTS START ==========="


class DevOnlyHookTest(PolicyHookTestBase):
    config_name = "dev-only"

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
        self.create_untagged_dev_release()
        self.create_optional_tagged_dev_release()

    def create_rejection_test_fixtures(self) -> None:
        pass

    def mark_rejection_tests_start(self) -> None:
        self.start_marker_sha = self.commit_file("dev", "TEST_START.txt", START_SYMBOL + "\n", START_SYMBOL)
        self.merge_to("dev", "main", message=START_SYMBOL)
        self.marker_dev_sha = self.rev_parse("dev")

    def run_rejection_tests(self) -> None:
        self.expect_rejected(["branch", "feat/demo", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_rejected(["branch", "release/demo", "dev"], "BRANCH_NAME_NOT_ALLOWED")

        self.git("checkout", "main")
        self.expect_rejected(
            ["commit", "--allow-empty", "-m", "direct main commit"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
        )

        self.git("checkout", "dev")
        self.git("commit", "--allow-empty", "-m", "direct dev commit")
        self.direct_dev_sha = self.rev_parse("dev")

        self.expect_rejected(["tag", "v1.1", self.marker_dev_sha], "TAG_SOURCE_TAG_PATTERN_MISMATCH")
        self.expect_rejected(["tag", "V1.1.0", self.marker_dev_sha], "TAG_SOURCE_TAG_PATTERN_MISMATCH")
        self.expect_rejected(["tag", "V2.0", "main"], "TAG_TARGET_NOT_SOURCE_HISTORY")
        self.expect_rejected(["tag", "V1.1", self.direct_dev_sha], "TAG_REQUIRED_TARGETS_MISSING")

    def checkout_final_branch(self) -> None:
        self.git("checkout", "dev")

    def create_untagged_dev_release(self) -> None:
        self.untagged_release_sha = self.commit_file(
            "dev",
            "untagged-dev-release.txt",
            "untagged dev release\n",
            "untagged dev release",
        )
        self.merge_to("dev", "main")
        self.assert_is_ancestor(self.untagged_release_sha, "main")
        self.assert_pending_tag_count(0)

    def create_optional_tagged_dev_release(self) -> None:
        self.tagged_release_sha = self.commit_file(
            "dev",
            "tagged-dev-release.txt",
            "tagged dev release\n",
            "tagged dev release",
        )
        self.merge_to("dev", "main")
        self.assert_is_ancestor(self.tagged_release_sha, "main")
        self.assert_pending_tag_count(0)

        self.dev_after_release_sha = self.commit_file(
            "dev",
            "dev-after-release.txt",
            "dev after release\n",
            "dev after release",
        )
        self.tag("V1.0", self.tagged_release_sha)
        self.assert_pending_tag_count(0)

    def state(self) -> dict[str, Any]:
        return json.loads((self.repo / ".git" / "git-flow-guard-state.json").read_text(encoding="utf-8"))

    def assert_pending_tag_count(self, expected: int) -> None:
        pending_tags = self.state().get("pending_tags", {})
        if len(pending_tags) != expected:
            raise AssertionError(f"{self.name}: expected {expected} pending tags, got {json.dumps(pending_tags, indent=2, sort_keys=True)}")


TEST_CASE = DevOnlyHookTest
