from __future__ import annotations

from configs.test_base import PolicyHookTestBase


START_SYMBOL = "=========== GIT FLOW GUARD REJECTION TESTS START ==========="


class InfraFeatReleaseHookTest(PolicyHookTestBase):
    config_name = "infra-feat-release"

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
        self.create_infra_flow()
        self.create_feature_flow()
        self.create_release_flow()
        self.create_hotfix_flow()

    def create_rejection_test_fixtures(self) -> None:
        self.create_infra_reject_to_main_fixture()
        self.create_feat_reject_to_main_fixture()
        self.create_release_reject_main_before_dev_fixture()
        self.create_old_release_fixture()
        self.create_hotfix_wrong_line_fixture()

    def mark_rejection_tests_start(self) -> None:
        branch = "feat/rejection-boundary"
        self.create_branch(branch, "dev")
        self.commit_file(branch, "TEST_START.txt", START_SYMBOL + "\n", "prepare rejection test boundary")
        self.merge_to(branch, "dev", message=START_SYMBOL)

    def run_rejection_tests(self) -> None:
        self.expect_rejected(["branch", "bug/demo", "dev"], "BRANCH_NAME_NOT_ALLOWED")
        self.expect_rejected(["branch", "release/from-main", "main"], "BRANCH_SOURCE_MISMATCH")
        self.expect_rejected(["branch", "hotfix/from-dev", "dev"], "BRANCH_SOURCE_MISMATCH")

        self.git("checkout", "main")
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", "infra/reject-to-main"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
            cleanup=self.cleanup_merge_state,
        )

        self.git("checkout", "main")
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", "feat/reject-to-main"],
            "PROTECTED_REF_NO_ALLOWED_SOURCE",
            cleanup=self.cleanup_merge_state,
        )

        self.expect_rejected(["tag", "release-1.0.0", "main"], "TAG_NAME_NOT_ALLOWED")
        self.expect_rejected(
            ["tag", "v1.1.2", self.release_sha],
            "TAG_SOURCE_TAG_PATTERN_MISMATCH",
        )
        self.expect_rejected(
            ["tag", "v1.2.2", self.hotfix_wrong_line_sha],
            "TAG_VERSION_LINE_MISMATCH",
        )
        self.expect_rejected(["tag", "v0.9.0", self.old_release_sha], "TAG_VERSION_NOT_INCREMENTAL")

        self.git("checkout", "main")
        self.expect_rejected(
            ["merge", "--no-ff", "--no-edit", "release/reject-main-before-dev"],
            "MULTI_TARGET_ORDER",
            cleanup=self.cleanup_merge_state,
        )

    def checkout_final_branch(self) -> None:
        self.git("checkout", "dev")

    def create_infra_flow(self) -> None:
        branch = "infra/pipeline"
        self.create_branch(branch, "dev")
        sha = self.commit_file(branch, "infra.txt", "infra\n", "infra work")
        self.merge_to(branch, "dev")
        self.assert_is_ancestor(sha, "dev")

    def create_feature_flow(self) -> None:
        branch = "feat/export"
        self.create_branch(branch, "dev")
        sha = self.commit_file(branch, "feature.txt", "feature\n", "feature work")
        self.merge_to(branch, "dev")
        self.assert_is_ancestor(sha, "dev")

    def create_release_flow(self) -> None:
        branch = "release/1.1"
        self.create_branch(branch, "dev")
        self.release_sha = self.commit_file(branch, "release-1.1.txt", "release 1.1\n", "release 1.1")
        self.merge_to(branch, "dev")
        self.merge_to(branch, "main")
        self.tag("v1.1.0", self.release_sha)
        self.assert_is_ancestor(self.release_sha, "dev")
        self.assert_is_ancestor(self.release_sha, "main")

    def create_hotfix_flow(self) -> None:
        branch = "hotfix/1.1.1"
        self.create_branch(branch, "main")
        self.hotfix_sha = self.commit_file(branch, "hotfix-1.1.1.txt", "hotfix 1.1.1\n", "hotfix 1.1.1")
        self.merge_to(branch, "dev")
        self.merge_to(branch, "main")
        self.tag("v1.1.1", self.hotfix_sha)
        self.assert_is_ancestor(self.hotfix_sha, "dev")
        self.assert_is_ancestor(self.hotfix_sha, "main")

    def create_infra_reject_to_main_fixture(self) -> None:
        branch = "infra/reject-to-main"
        self.create_branch(branch, "dev")
        self.commit_file(branch, "infra-reject.txt", "infra reject\n", "fixture infra reject")

    def create_feat_reject_to_main_fixture(self) -> None:
        branch = "feat/reject-to-main"
        self.create_branch(branch, "dev")
        self.commit_file(branch, "feature-reject.txt", "feature reject\n", "fixture feat reject")

    def create_release_reject_main_before_dev_fixture(self) -> None:
        branch = "release/reject-main-before-dev"
        self.create_branch(branch, "dev")
        self.unmerged_release_sha = self.commit_file(
            branch,
            "release-reject-order.txt",
            "release reject order\n",
            "fixture release order",
        )

    def create_old_release_fixture(self) -> None:
        branch = "release/0.9"
        self.create_branch(branch, "dev")
        self.old_release_sha = self.commit_file(branch, "release-0.9.txt", "release 0.9\n", "fixture release 0.9")
        self.merge_to(branch, "dev")
        self.merge_to(branch, "main")

    def create_hotfix_wrong_line_fixture(self) -> None:
        branch = "hotfix/wrong-line"
        self.create_branch(branch, "main")
        self.hotfix_wrong_line_sha = self.commit_file(
            branch,
            "hotfix-wrong-line.txt",
            "hotfix wrong line\n",
            "fixture hotfix wrong line",
        )
        self.merge_to(branch, "dev")
        self.merge_to(branch, "main")


TEST_CASE = InfraFeatReleaseHookTest
