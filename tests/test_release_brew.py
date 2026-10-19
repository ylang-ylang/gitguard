"""Stateful tap transactions, real candidate hashing, and fail-closed checks."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import release_brew as brew


def spec(version="1.18"):
    tag = "V" + version
    asset = {"kind": "sdist", "filename": f"git_guard-{version}.tar.gz", "size": 100,
             "sha256": "a" * 64,
             "url": f"https://github.com/{brew.SOURCE_REPO}/releases/download/{tag}/git_guard-{version}.tar.gz"}
    return {"tag": tag, "version": version, "tag_object_sha": "b" * 40,
            "commit_sha": "c" * 40, "release_id": 42, "manifest_sha256": "d" * 64,
            "manifest": {"assets": [asset]}, "assets": [dict(asset, id=101)]}


class TapModel:
    """Model persistent Git trees/refs/PRs; merges preserve concurrent base edits.

    This is deliberately not a call-echo mock: writes create immutable objects,
    reads consult those objects, PR files derive from tree differences, and the
    merge endpoint enforces expected SHA and three-way conflict detection.
    """
    def __init__(self, current=None):
        self.objects = {}
        self.refs = {}
        self.pulls = {}
        self.actor = 23
        self.merges = 0
        self.check_reads = 0
        self.before_checks = None
        self.check_transform = None
        self.pull_transform = None
        self.file_transform = None
        self.portmap = "unchanged portmap\n"
        initial = {"Formula/portmap.rb": self.portmap}
        if current is not None:
            initial[brew.FORMULA_PATH] = current
        tree = self.store(initial)
        self.refs["main"] = self.store({"tree": {"sha": tree}, "parents": [], "message": "initial"})

    def store(self, value):
        oid = hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()
        self.objects[oid] = deepcopy(value)
        return oid

    def tree_at(self, commit):
        return self.objects[self.objects[commit]["tree"]["sha"]]

    def advance_main(self, path, text):
        old = self.refs["main"]
        tree = dict(self.tree_at(old))
        tree[path] = text
        tree_sha = self.store(tree)
        self.refs["main"] = self.store({"tree": {"sha": tree_sha}, "parents": [{"sha": old}], "message": "another project"})

    def live_pull(self, number):
        pull = deepcopy(self.pulls[number])
        branch = pull["head"]["ref"]
        pull["head"]["sha"] = self.refs[branch]
        pull["base"]["sha"] = self.refs["main"]
        pull["changed_files"] = len(self.diff(number))
        if self.pull_transform:
            self.pull_transform(pull)
        return pull

    def diff(self, number):
        pull = self.pulls[number]
        commit = self.refs[pull["head"]["ref"]]
        parent = self.objects[commit]["parents"][0]["sha"]
        before, after = self.tree_at(parent), self.tree_at(commit)
        return [{"filename": path, "status": "modified" if path in before else "added"}
                for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)]

    def request(self, method, url, data=None, allow_404=False):
        parsed = urlsplit(url)
        path, query = parsed.path, parse_qs(parsed.query)
        if method == "GET" and path == "/user":
            return {"id": self.actor}
        if path == brew.ROOT:
            return {"default_branch": "main"}
        assert path.startswith(brew.ROOT + "/"), path
        path = path[len(brew.ROOT):]
        if method == "GET" and path.startswith("/git/ref/heads/"):
            branch = path[len("/git/ref/heads/"):]
            if branch not in self.refs:
                assert allow_404
                return None
            return {"object": {"sha": self.refs[branch]}}
        if method == "GET" and path.startswith("/contents/"):
            filename = path[len("/contents/"):]
            tree = self.tree_at(query["ref"][0])
            if filename not in tree:
                assert allow_404
                return None
            return {"type": "file", "path": filename, "encoding": "base64",
                    "content": base64.b64encode(tree[filename].encode()).decode()}
        if method == "GET" and path.startswith("/git/commits/"):
            return deepcopy(self.objects[path.rsplit("/", 1)[1]])
        if method == "POST" and path == "/git/blobs":
            return {"sha": self.store(data["content"])}
        if method == "POST" and path == "/git/trees":
            tree = dict(self.objects[data["base_tree"]])
            for entry in data["tree"]:
                assert entry["mode"] == "100644" and entry["type"] == "blob"
                tree[entry["path"]] = self.objects[entry["sha"]]
            return {"sha": self.store(tree)}
        if method == "POST" and path == "/git/commits":
            return {"sha": self.store({"tree": {"sha": data["tree"]}, "message": data["message"],
                                       "parents": [{"sha": p} for p in data["parents"]]})}
        if method == "POST" and path == "/git/refs":
            branch = data["ref"][len("refs/heads/"):]
            if branch in self.refs:
                raise brew.ReleaseError("ref already exists")
            self.refs[branch] = data["sha"]
            return {"object": {"sha": data["sha"]}}
        if path == "/pulls":
            if method == "GET":
                return [self.live_pull(n) for n in self.pulls]
            number = len(self.pulls) + 1
            self.pulls[number] = {"number": number, "body": data["body"], "user": {"id": self.actor},
                                  "state": "open", "draft": False, "merged_at": None, "mergeable": True,
                                  "head": {"ref": data["head"], "repo": {"full_name": brew.TAP_REPO}},
                                  "base": {"ref": data["base"], "repo": {"full_name": brew.TAP_REPO}}}
            return self.live_pull(number)
        if path.startswith("/pulls/"):
            parts = path.split("/")
            number = int(parts[2])
            if len(parts) == 3:
                return self.live_pull(number)
            if parts[3] == "files":
                files = self.diff(number)
                return self.file_transform(files) if self.file_transform else files
            if parts[3] == "merge":
                pull = self.live_pull(number)
                assert data["sha"] == pull["head"]["sha"]
                commit = data["sha"]
                original = self.tree_at(self.objects[commit]["parents"][0]["sha"])
                target = self.tree_at(commit)
                current = dict(self.tree_at(self.refs["main"]))
                for item in self.diff(number):
                    filename = item["filename"]
                    if current.get(filename) != original.get(filename):
                        raise brew.ReleaseError("merge conflict")
                    current[filename] = target[filename]
                tree = self.store(current)
                self.refs["main"] = self.store({"tree": {"sha": tree}, "parents": [{"sha": self.refs["main"]}, {"sha": commit}], "message": "merge"})
                self.pulls[number]["merged_at"] = "now"
                self.pulls[number]["state"] = "closed"
                self.merges += 1
                return {"merged": True}
        if path.startswith("/commits/"):
            commit = path.split("/")[2]
            if path.endswith("/statuses"):
                return []
            self.check_reads += 1
            if self.before_checks:
                self.before_checks(self)
                self.before_checks = None
            runs = [{"name": brew.REQUIRED_CHECK, "head_sha": commit, "status": "completed", "conclusion": "success"}]
            if self.check_transform:
                runs = self.check_transform(runs, self.check_reads)
            return {"total_count": len(runs), "check_runs": runs}
        raise AssertionError((method, url, data))


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.release = spec()
        self.tap = TapModel()

    def update(self, **kwargs):
        return brew.update_tap(None, self.tap, self.release["tag"], timeout=0,
                               verify=kwargs.pop("verify", lambda source, tag: deepcopy(self.release)), **kwargs)

    def test_first_formula_and_published_retry(self):
        self.assertEqual(self.update(), "merged")
        self.assertEqual(self.update(), "already-current")
        self.assertEqual(self.tap.merges, 1)
        self.assertEqual(self.tap.tree_at(self.tap.refs["main"])["Formula/portmap.rb"], self.tap.portmap)

    def test_other_project_main_update_preserved(self):
        self.tap.before_checks = lambda tap: tap.advance_main("Formula/portmap.rb", "portmap next")
        self.assertEqual(self.update(), "merged")
        self.assertEqual(self.tap.tree_at(self.tap.refs["main"])["Formula/portmap.rb"], "portmap next")

    def test_owned_pr_can_retry_missing_checks(self):
        self.tap.check_transform = lambda runs, n: []
        with self.assertRaisesRegex(brew.ReleaseError, "timed out"):
            self.update()
        self.tap.check_transform = None
        self.assertEqual(self.update(), "merged")
        self.assertEqual(len(self.tap.pulls), 1)

    def test_orphan_branch_never_adopted(self):
        self.tap.refs["brew/git-guard-V1.18"] = self.tap.refs["main"]
        with self.assertRaisesRegex(brew.ReleaseError, "refusing adoption"):
            self.update()
        self.assertEqual(self.tap.merges, 0)

    def test_foreign_pr_author_or_identity_never_adopted(self):
        for field in ("user", "body", "base", "head"):
            with self.subTest(field=field):
                self.tap = TapModel()
                self.tap.check_transform = lambda runs, n: []
                with self.assertRaises(brew.ReleaseError):
                    self.update()
                pull = self.tap.pulls[1]
                if field == "user":
                    pull["user"]["id"] += 1
                elif field == "body":
                    pull["body"] += "changed"
                else:
                    pull[field]["repo"]["full_name"] = "attacker/tap"
                self.tap.check_transform = None
                with self.assertRaises(brew.ReleaseError):
                    self.update()
                self.assertEqual(self.tap.merges, 0)

    def test_replaced_release_asset_blocks_merge(self):
        calls = 0
        def verify(source, tag):
            nonlocal calls
            calls += 1
            result = deepcopy(self.release)
            if calls > 1:
                result["assets"][0]["id"] += 1
            return result
        with self.assertRaisesRegex(brew.ReleaseError, "identity changed"):
            self.update(verify=verify)
        self.assertEqual(self.tap.merges, 0)

    def test_downward_or_same_version_identity_change_fails(self):
        for version in ("1.19", "1.18"):
            self.tap = TapModel(brew.release_formula(spec(version)).replace('sha256 "a', 'sha256 "b'))
            with self.assertRaises(brew.ReleaseError):
                self.update()
            self.assertFalse(self.tap.pulls)

    def test_concurrent_formula_update_blocks_merge(self):
        self.tap.before_checks = lambda tap: tap.advance_main(brew.FORMULA_PATH, brew.release_formula(spec("1.19")))
        with self.assertRaisesRegex(brew.ReleaseError, "downgrade"):
            self.update()
        self.assertEqual(self.tap.merges, 0)

    def test_branch_push_during_checks_blocks_merge(self):
        def move(tap):
            branch = "brew/git-guard-V1.18"
            commit = deepcopy(tap.objects[tap.refs[branch]])
            commit["message"] = "foreign push"
            tap.refs[branch] = tap.store(commit)
        self.tap.before_checks = move
        with self.assertRaisesRegex(brew.ReleaseError, "head changed"):
            self.update()
        self.assertEqual(self.tap.merges, 0)

    def test_rerun_started_after_success_blocks_merge(self):
        def checks(runs, count):
            if count > 1:
                runs[0].update(status="in_progress", conclusion=None)
            return runs
        self.tap.check_transform = checks
        with self.assertRaisesRegex(brew.ReleaseError, "timed out"):
            self.update()
        self.assertEqual(self.tap.merges, 0)

    def test_complete_diff_rejects_extra_missing_renamed_files(self):
        transformations = [lambda files: files + [{"filename": "Formula/portmap.rb", "status": "modified"}],
                           lambda files: [],
                           lambda files: [dict(files[0], previous_filename="Formula/portmap.rb")]]
        for transform in transformations:
            self.tap = TapModel()
            self.tap.file_transform = transform
            with self.assertRaises(brew.ReleaseError):
                self.update()
            self.assertEqual(self.tap.merges, 0)


class InputAndCheckTests(unittest.TestCase):
    def test_invalid_tags_and_urls(self):
        for version in ("01.18", "1.018", "1.18.0", "1.18\n", "../1.18"):
            with self.assertRaises(brew.ReleaseError):
                brew.render_formula(version, "https://example.invalid", "a" * 64)
        for url in ("https://github.com/other/repo/releases/download/V1.18/git_guard-1.18.tar.gz",
                    "https://github.com/ylang-ylang/gitguard/archive/refs/tags/V1.18.tar.gz",
                    "file:///tmp/candidate.tar.gz"):
            with self.assertRaises(brew.ReleaseError):
                brew.render_formula("1.18", url, "a" * 64)

    def test_no_token_fallback(self):
        with patch.dict("os.environ", {"HOMEBREW_TAP_TOKEN": "tap-only"}, clear=True):
            with self.assertRaisesRegex(brew.ReleaseError, "GITHUB_TOKEN"):
                brew.main(["update-tap", "--tag", "V1.18"])
        with patch.dict("os.environ", {"GITHUB_TOKEN": "source-only"}, clear=True):
            with self.assertRaisesRegex(brew.ReleaseError, "HOMEBREW_TAP_TOKEN"):
                brew.main(["update-tap", "--tag", "V1.18"])

    def test_required_check_must_be_explicit_success_at_exact_head(self):
        commit = "a" * 40
        run = {"name": brew.REQUIRED_CHECK, "head_sha": commit, "status": "completed", "conclusion": "success"}
        self.assertTrue(brew.checks_ready([run], [], commit))
        self.assertFalse(brew.checks_ready([], [], commit))
        for conclusion in ("failure", "neutral", "skipped", "cancelled", None):
            with self.assertRaises(brew.ReleaseError):
                brew.checks_ready([dict(run, conclusion=conclusion)], [], commit)
        with self.assertRaises(brew.ReleaseError):
            brew.checks_ready([dict(run, head_sha="b" * 40)], [], commit)
        self.assertFalse(brew.checks_ready([run, dict(run, status="queued", conclusion=None)], [], commit))

    def test_latest_status_supersedes_old_success(self):
        commit = "a" * 40
        run = {"name": brew.REQUIRED_CHECK, "head_sha": commit, "status": "completed", "conclusion": "success"}
        statuses = [{"id": 3, "context": "lint", "state": "pending"},
                    {"id": 2, "context": "lint", "state": "success"}]
        self.assertFalse(brew.checks_ready([run], statuses, commit))

    def test_local_renderer_hashes_real_candidate_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "git_guard-1.18.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                metadata = b"Metadata-Version: 2.1\nName: git-guard\nVersion: 1.18\n"
                member = tarfile.TarInfo("git_guard-1.18/PKG-INFO")
                member.size = len(metadata)
                output.addfile(member, io.BytesIO(metadata))
            text = brew.render_local(archive, "1.18")
            self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest(), text)
            self.assertIn(archive.as_uri(), text)
            self.assertIn("LOCAL VALIDATION ONLY", text)
            self.assertIn("virtualenv_install_with_resources", text)
            self.assertIn('depends_on "python@3.13"', text)
            self.assertIn("PROTECTED_REF_DELETE", text)
            self.assertNotIn("/home/linuxbrew", text)
            with self.assertRaises(brew.ReleaseError):
                brew.render_local(archive, "1.19")


if __name__ == "__main__":
    unittest.main()
