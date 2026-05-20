from __future__ import annotations

from configs.test_base import PolicyHookTestBase


START_SYMBOL = "=========== GIT FLOW GUARD REJECTION TESTS START ==========="


class DevOnlyHookTest(PolicyHookTestBase):
    config_name = "dev-release"

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
        self.create_direct_dev_work()
        self.create_release_flow()

    def create_rejection_test_fixtures(self) -> None:
        self.create_unmerged_release_fixture()

    def mark_rejection_tests_start(self) -> None:
        self.git("checkout", "dev")
        self.write_file("TEST_START.txt", START_SYMBOL + "\n")
        self.git("add", "TEST_START.txt")
        self.git("commit", "-m", START_SYMBOL)

    def run_rejection_tests(self) -> None:
        self.expect_rejected(["branch", "feat/demo", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_rejected(["branch", "release/from-main", "main"], "BRANCH_SOURCE_MISMATCH")

        self.git("checkout", "main")
        self.expect_rejected(
            ["commit", "--allow-empty", "-m", "direct main commit"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
        )
        self.expect_rejected(["tag", "V2.0", "main"], "TAG_REQUIRED_TARGETS_MISSING")

        self.expect_rejected(["tag", "v1.2", self.release_sha], "TAG_SOURCE_TAG_PATTERN_MISMATCH")
        self.expect_rejected(["tag", "V1.2.0", self.release_sha], "TAG_SOURCE_TAG_PATTERN_MISMATCH")
        self.expect_rejected(["tag", "V1.0", self.release_sha], "TAG_VERSION_NOT_INCREMENTAL")
        self.expect_rejected(["tag", "V1.2", self.unmerged_release_sha], "TAG_REQUIRED_TARGETS_MISSING")

        self.git("checkout", "main")
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", "dev"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
            cleanup=self.cleanup_merge_state,
        )

    def checkout_final_branch(self) -> None:
        self.git("checkout", "dev")

    def create_direct_dev_work(self) -> None:
        self.dev_work_sha = self.commit_file("dev", "dev-work.txt", "dev work\n", "direct dev work")
        self.assert_is_ancestor(self.dev_work_sha, "dev")

    def create_release_flow(self) -> None:
        branch = "release/1.1"
        self.create_branch(branch, "dev")
        self.release_sha = self.commit_file(branch, "release-1.1.txt", "release 1.1\n", "release 1.1")
        self.merge_to(branch, "dev")
        self.merge_to(branch, "main")
        self.tag("V1.1", self.release_sha)
        self.assert_is_ancestor(self.release_sha, "dev")
        self.assert_is_ancestor(self.release_sha, "main")

    def create_unmerged_release_fixture(self) -> None:
        branch = "release/unmerged"
        self.create_branch(branch, "dev")
        self.unmerged_release_sha = self.commit_file(
            branch,
            "release-unmerged.txt",
            "release unmerged\n",
            "fixture unmerged release",
        )


TEST_CASE = DevOnlyHookTest
