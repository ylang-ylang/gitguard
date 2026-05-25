from __future__ import annotations

from configs.test_base import PolicyHookTestBase


START_SYMBOL = "=========== GIT GUARD REJECTION TESTS START ==========="


class BasicFeatureReleaseHookTest(PolicyHookTestBase):
    config_name = "dev-feat-release-hotfix"

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
        self.create_feature_flow()
        self.create_release_flow()
        self.create_hotfix_flow()

    def create_rejection_test_fixtures(self) -> None:
        self.create_feat_reject_to_main_fixture()
        self.create_release_reject_main_before_dev_fixture()
        self.create_old_release_fixture()

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
        self.expect_merge_rejected("feat/reject-to-main", "PROTECTED_REF_NO_ALLOWED_SOURCE")

        self.expect_rejected(["tag", "release-1.0.0", "main"], "TAG_TARGET_TAG_PATTERN_MISMATCH")
        self.expect_rejected(
            ["tag", "v1.1.2", self.release_main_sha],
            "TAG_TARGET_NOT_TARGET_HEAD",
        )
        self.expect_rejected(["tag", "v0.9.0", self.old_release_main_sha], "TAG_VERSION_NOT_INCREMENTAL")
        self.assert_hotfix_wrong_line_rejected()

        self.git("checkout", "main")
        self.expect_merge_rejected("release/reject-main-before-dev", "MULTI_TARGET_ORDER")

    def checkout_final_branch(self) -> None:
        self.git("checkout", "dev")

    def create_feature_flow(self) -> None:
        branch = "feat/export"
        self.create_branch(branch, "dev")
        feature_sha = self.commit_file(branch, "feature.txt", "feature\n", "feature work")
        dev_sha = self.commit_file("dev", "dev-during-feature.txt", "dev during feature\n", "dev advances during feature work")
        self.merge_to("dev", branch)
        self.merge_to(branch, "dev")
        self.assert_is_ancestor(feature_sha, "dev")
        self.assert_is_ancestor(dev_sha, branch)

    def create_release_flow(self) -> None:
        branch = "release/1.1"
        self.create_branch(branch, "dev")
        self.release_sha = self.commit_file(branch, "release-1.1.txt", "release 1.1\n", "release 1.1")
        self.merge_to(branch, "dev")
        self.merge_to(branch, "main")
        self.release_main_sha = self.rev_parse("main")
        self.tag("v1.1.0", self.release_main_sha)
        self.assert_is_ancestor(self.release_sha, "dev")
        self.assert_is_ancestor(self.release_sha, "main")

    def create_hotfix_flow(self) -> None:
        branch = "hotfix/1.1.1"
        self.create_branch(branch, "main")
        self.hotfix_sha = self.commit_file(branch, "hotfix-1.1.1.txt", "hotfix 1.1.1\n", "hotfix 1.1.1")
        self.merge_to(branch, "dev")
        self.merge_to(branch, "main")
        self.hotfix_main_sha = self.rev_parse("main")
        self.tag("v1.1.1", self.hotfix_main_sha)
        self.assert_is_ancestor(self.hotfix_sha, "dev")
        self.assert_is_ancestor(self.hotfix_sha, "main")

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
        self.old_release_main_sha = self.rev_parse("main")
        self.tag("v1.2.0", self.old_release_main_sha)

    def assert_hotfix_wrong_line_rejected(self) -> None:
        branch = "hotfix/wrong-line-reject"
        self.create_branch(branch, "main")
        self.hotfix_wrong_line_sha = self.commit_file(
            branch,
            "hotfix-wrong-line.txt",
            "hotfix wrong line\n",
            "fixture hotfix wrong line",
        )
        self.merge_to(branch, "dev")
        self.merge_to(branch, "main")
        self.hotfix_wrong_line_main_sha = self.rev_parse("main")
        self.expect_rejected(["tag", "v1.3.2", self.hotfix_wrong_line_main_sha], "TAG_VERSION_LINE_MISMATCH")
        self.tag("v1.2.1", self.hotfix_wrong_line_main_sha)


TEST_CASE = BasicFeatureReleaseHookTest
