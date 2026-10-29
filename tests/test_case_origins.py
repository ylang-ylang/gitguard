"""Case origins compile from long-lived declarations and validate actual Git tips."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.mermaid import extract_gitgraph_block, graph_to_policy, parse_gitgraph, ref_pattern, ref_regex
from src.runtime.branch_policy import update_committed_state, validate_prepared
from src.runtime.common import HookReject, RefUpdate, ZERO
from src.runtime.state import load_state


GRAPH = """gitGraph
    commit
    branch dev
    checkout dev
    branch feat/*
    checkout dev
    branch release/*
    checkout main
    branch hotfix/*
    checkout main
    branch case/*/*
"""


def compile_policy(graph_text=GRAPH):
    return graph_to_policy(parse_gitgraph(graph_text), "contribution.md")


class CaseOriginCompilerTests(unittest.TestCase):
    def test_every_case_policy_expands_only_case_origins(self):
        paths = sorted((ROOT / "configs").glob("*/contribution.md"))
        paths.append(ROOT / ".git-guard" / "contribution.md")
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(policy=str(path.relative_to(ROOT))):
                graph = parse_gitgraph(extract_gitgraph_block(path.read_text(encoding="utf-8")))
                policy = graph_to_policy(graph, path.name)
                expected = []
                for edge in graph.branch_from:
                    sources = policy["branches"]["long_lived"] if edge.target == "case/*/*" else [edge.source]
                    for source in sources:
                        expected.append({
                            "source": source,
                            "target": edge.target,
                            "source_ref": ref_pattern(source),
                            "target_ref": ref_pattern(edge.target),
                            "target_ref_regex": ref_regex(edge.target),
                        })
                self.assertEqual(policy["branch_from"], expected)
                without_case_origin = graph_to_policy(
                    replace(graph, branch_from=[edge for edge in graph.branch_from if edge.target != "case/*/*"]),
                    path.name,
                )
                for key in policy.keys() - {"branch_from"}:
                    self.assertEqual(policy[key], without_case_origin[key], key)

    def test_additional_long_lived_branch_is_an_origin_in_declaration_order(self):
        policy = compile_policy(GRAPH + "    checkout dev\n    branch staging\n")
        origins = [edge["source"] for edge in policy["branch_from"] if edge["target"] == "case/*/*"]
        self.assertEqual(origins, ["main", "dev", "staging"])
        self.assertEqual(policy["branches"]["families"], ["feat/*", "release/*", "hotfix/*", "case/*/*"])


class CaseOriginRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="git-guard-case-origins-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        home = self.root / "home"
        home.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "GG_"))}
        env.update(
            HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Case Origin Test", GIT_AUTHOR_EMAIL="case@example.invalid",
            GIT_COMMITTER_NAME="Case Origin Test", GIT_COMMITTER_EMAIL="case@example.invalid",
            GIT_AUTHOR_DATE="2026-01-01T00:00:00+0000", GIT_COMMITTER_DATE="2026-01-01T00:00:00+0000",
        )
        environment = patch.dict(os.environ, env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.git("init", "--template=", "-b", "main", str(self.repo), cwd=self.root)
        self.tree = self.git("mktree", input="")
        self.old_main = self.commit("initial")
        self.main = self.commit("main tip", self.old_main)
        self.dev = self.commit("dev tip", self.old_main)
        self.feature = self.commit("feature tip", self.dev)
        self.git("update-ref", "refs/heads/main", self.main)
        self.git("update-ref", "refs/heads/dev", self.dev)
        self.git("update-ref", "refs/heads/feat/unique", self.feature)
        self.state_path = self.repo / ".git" / "case-state.json"
        self.policy = compile_policy()

    def git(self, *args, cwd=None, input=None):
        result = subprocess.run(
            ["git", *args], cwd=cwd or self.repo, input=input,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, f"git {' '.join(args)}:\n{result.stdout}\n{result.stderr}")
        return result.stdout.strip()

    def commit(self, message, parent=None):
        args = ["commit-tree", self.tree, "-m", message]
        if parent:
            args.extend(["-p", parent])
        return self.git(*args)

    def create(self, branch, sha, source):
        update = RefUpdate(old=ZERO, new=sha, ref=f"refs/heads/{branch}")
        validate_prepared(self.repo, self.policy, {}, self.state_path, [update])
        self.git("update-ref", update.ref, update.new, ZERO)
        update_committed_state(self.repo, self.policy, self.state_path, [update])
        base = load_state(self.state_path)["branch_bases"][update.ref]
        self.assertEqual(base, {"source_ref": f"refs/heads/{source}", "base_sha": sha, "base_release_tag": None})
        return update

    def reject(self, branch, sha, code, source=None):
        update = RefUpdate(old=ZERO, new=sha, ref=f"refs/heads/{branch}")
        with self.assertRaises(HookReject) as caught:
            validate_prepared(self.repo, self.policy, {}, self.state_path, [update])
        self.assertEqual(caught.exception.code, code)
        if source:
            self.assertEqual(caught.exception.context["source_ref"], f"refs/heads/{source}")
        self.assertFalse(self.state_path.exists())

    def test_divergent_main_and_dev_tips_record_the_matching_origin(self):
        self.assertNotEqual(self.main, self.dev)
        self.create("case/main/topic", self.main, "main")
        self.create("case/dev/topic", self.dev, "dev")

    def test_additional_declared_long_lived_origin(self):
        self.policy = compile_policy(GRAPH + "    checkout dev\n    branch staging\n")
        staging = self.commit("staging tip", self.old_main)
        self.git("update-ref", "refs/heads/staging", staging)
        self.create("case/staging/topic", staging, "staging")

    def test_equal_tips_use_declaration_order(self):
        self.git("update-ref", "refs/heads/dev", self.main)
        self.create("case/shared/first", self.main, "main")
        self.policy["branch_from"].reverse()
        self.create("case/shared/reversed", self.main, "dev")

    def test_unique_feature_tip_is_not_a_long_lived_origin(self):
        self.reject("case/feature/topic", self.feature, "BRANCH_SOURCE_MISMATCH", "main")

    def test_old_main_commit_is_not_an_origin_tip(self):
        self.reject("case/old/topic", self.old_main, "BRANCH_SOURCE_MISMATCH", "main")

    def test_missing_first_source_does_not_hide_valid_second_source(self):
        self.git("update-ref", "-d", "refs/heads/main")
        self.create("case/dev/missing-main", self.dev, "dev")

    def test_missing_second_source_does_not_hide_valid_first_source(self):
        self.git("update-ref", "-d", "refs/heads/dev")
        self.create("case/main/missing-dev", self.main, "main")

    def test_no_existing_sources_reports_missing(self):
        self.git("update-ref", "-d", "refs/heads/main")
        self.git("update-ref", "-d", "refs/heads/dev")
        self.reject("case/missing/topic", self.feature, "BRANCH_SOURCE_MISSING", "main")

    def test_existing_nonmatching_second_source_reports_mismatch(self):
        self.git("update-ref", "-d", "refs/heads/main")
        self.reject("case/mismatch/topic", self.old_main, "BRANCH_SOURCE_MISMATCH", "dev")

    def test_no_target_matching_edge_reports_creation_not_allowed(self):
        self.policy["branch_from"] = [edge for edge in self.policy["branch_from"] if edge["target"] != "case/*/*"]
        self.reject("case/absent/topic", self.main, "BRANCH_CREATION_NOT_ALLOWED")

    def test_non_case_families_keep_their_single_origin(self):
        for family, allowed_sha, source, other_sha in (
            ("feat", self.dev, "dev", self.main),
            ("release", self.dev, "dev", self.main),
            ("hotfix", self.main, "main", self.dev),
        ):
            with self.subTest(family=family):
                self.reject(f"{family}/wrong-origin", other_sha, "BRANCH_SOURCE_MISMATCH", source)
        for family, sha, source in (("feat", self.dev, "dev"), ("release", self.dev, "dev"), ("hotfix", self.main, "main")):
            with self.subTest(family=family):
                self.create(f"{family}/allowed-origin", sha, source)

    def test_single_source_missing_preserves_diagnostic(self):
        self.git("update-ref", "-d", "refs/heads/dev")
        self.reject("feat/missing-origin", self.main, "BRANCH_SOURCE_MISSING", "dev")


if __name__ == "__main__":
    unittest.main()
