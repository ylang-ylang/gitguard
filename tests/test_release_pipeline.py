"""Offline behavioral tests: real Git identity, static source and byte verification."""
from __future__ import annotations

import base64
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from release_common import (GitHub, PREFIX, SOURCE_REPO, ReleaseError, _Redirect,
                            sha256, validate_remote_tag, validate_versions,
                            verify_published_release, version_for_tag)
from release_pipeline import plan, publish, recover_artifact, validate_bundle, validate_local_tag


class Service:
    """In-memory REST service with immutable artifact and byte-backed release assets."""

    def __init__(self):
        self.commit = "a" * 40
        self.tag_object = "b" * 40
        self.main = "c" * 40
        self.version = "1.18"
        self.status = "ahead"
        self.tag = "V1.18"
        self.identity = dict(tag=self.tag, version=self.version, tag_object_sha=self.tag_object, commit_sha=self.commit)
        self.document = None
        self.mutations = []
        self.downloads = []
        self.asset_bytes = {}
        self.expired = False
        self.fail_upload = None
        self.files = {}
        assets = []
        for name, kind, data in (("git_guard-1.18.tar.gz", "sdist", b"original source bytes"),
                                 ("git_guard-1.18-py3-none-any.whl", "wheel", b"original wheel bytes")):
            self.files[name] = data
            assets.append(dict(filename=name, kind=kind, size=len(data), sha256=sha256(data),
                               url=f"https://github.com/{SOURCE_REPO}/releases/download/{self.tag}/{name}"))
        self.manifest = dict(schema_version=1, version=self.version,
                             source=dict(repository=SOURCE_REPO, tag=self.tag, commit=self.commit), assets=assets)
        self.files["release.json"] = json.dumps(self.manifest).encode()
        self.files["SHA256SUMS"] = "".join(f"{a['sha256']}  {a['filename']}\n" for a in assets).encode()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in self.files.items():
                bundle.writestr(name, data)
        self.archive = archive.getvalue()
        self.anchor = dict(self.identity, artifact_id=100, run_id=200, run_attempt=1,
                           artifact_sha256=sha256(self.archive), manifest_sha256=sha256(self.files["release.json"]))

    def request(self, method, path, data=None, *, allow_404=False):
        if method != "GET":
            self.mutations.append((method, path))
        if path == f"{PREFIX}/git/ref/tags/{self.tag}":
            return dict(ref=f"refs/tags/{self.tag}", object=dict(type="tag", sha=self.tag_object))
        if path == f"{PREFIX}/git/tags/{self.tag_object}":
            return dict(object=dict(type="commit", sha=self.commit))
        if path == f"{PREFIX}/git/ref/heads/main":
            return dict(object=dict(type="commit", sha=self.main))
        if path.startswith(f"{PREFIX}/compare/"):
            return dict(status=self.status, merge_base_commit=dict(sha=self.commit if self.status in {"ahead", "identical"} else self.main))
        if path.startswith(f"{PREFIX}/contents/"):
            text = f'[project]\nversion = "{self.version}"\n' if "/pyproject.toml?" in path else f'__version__ = "{self.version}"\nraise RuntimeError("must not execute")\n'
            return dict(type="file", encoding="base64", content=base64.b64encode(text.encode()).decode())
        if path == f"{PREFIX}/releases/tags/{self.tag}":
            return copy.deepcopy(self.document) if self.document and not self.document["draft"] else None
        if path.startswith(f"{PREFIX}/releases?"):
            return [copy.deepcopy(self.document)] if self.document else []
        if path == f"{PREFIX}/actions/artifacts/100":
            return dict(id=100, name="release-assets-200-1", expired=self.expired, workflow_run=dict(id=200))
        if method == "POST" and path == f"{PREFIX}/releases":
            self.document = dict(data, id=300, assets=[])
            return copy.deepcopy(self.document)
        if method == "POST" and path.startswith("https://uploads.github.com"):
            name = path.split("?name=")[1]
            if self.fail_upload == name:
                raise ReleaseError("simulated interrupted upload")
            asset_id = 400 + len(self.asset_bytes)
            self.asset_bytes[asset_id] = data
            self.document["assets"].append(dict(id=asset_id, name=name, size=len(data), digest="sha256:" + sha256(data),
                state="uploaded", browser_download_url=f"https://github.com/{SOURCE_REPO}/releases/download/untagged-0123456789abcdef0123/{name}"))
            return copy.deepcopy(self.document["assets"][-1])
        if method == "PATCH" and path == f"{PREFIX}/releases/300":
            self.document.update(data)
            if not self.document["draft"]:
                for asset in self.document["assets"]:
                    asset["browser_download_url"] = f"https://github.com/{SOURCE_REPO}/releases/download/{self.tag}/{asset['name']}"
            return copy.deepcopy(self.document)
        raise AssertionError((method, path))

    def download(self, path):
        self.downloads.append(path)
        if path == f"{PREFIX}/actions/artifacts/100/zip":
            return self.archive
        asset_id = int(path.rsplit("/", 1)[1])
        for asset in self.document["assets"]:
            if asset["id"] == asset_id:
                asset["download_count"] = asset.get("download_count", 0) + 1
        return self.asset_bytes[asset_id]


class ReleaseTests(unittest.TestCase):
    def test_tag_mapping_is_exact(self):
        self.assertEqual(version_for_tag("V1.18"), "1.18")
        self.assertEqual(version_for_tag("V0.0"), "0.0")
        for tag in ("v1.18", "V01.18", "V1.018", "V1.18.0", " V1.18", "V1.18\n", "refs/tags/V1.18", "V1.18;id"):
            with self.subTest(tag=tag), self.assertRaises(ReleaseError):
                version_for_tag(tag)

    def test_static_versions_never_execute_source(self):
        validate_versions('[project]\nversion="1.18"', '__version__="1.18"\nraise RuntimeError("not imported")', "1.18")
        for cli in ('__version__="0.1.0"', '__version__=str(1.18)', '__version__="1.18"\n__version__="1.18"'):
            with self.assertRaises(ReleaseError):
                validate_versions('[project]\nversion="1.18"', cli, "1.18")

    def test_remote_off_main_and_metadata_mismatch(self):
        service = Service()
        self.assertEqual(validate_remote_tag(service, service.tag), service.identity)
        service.status = "diverged"
        with self.assertRaisesRegex(ReleaseError, "main history"):
            validate_remote_tag(service, service.tag)
        service.status = "ahead"
        service.version = "0.1.0"
        with self.assertRaisesRegex(ReleaseError, "versions"):
            validate_remote_tag(service, service.tag)

    def test_moved_annotated_tag_same_commit_is_rejected(self):
        service = Service()
        service.tag_object = "d" * 40
        with self.assertRaisesRegex(ReleaseError, "drifted"):
            publish(service, service.identity, service.anchor)
        self.assertEqual(service.mutations, [])

    def test_plan_push_identity_and_manual_existing_tag(self):
        service = Service()
        self.assertEqual(plan(service, service.tag, "push", service.commit)["mode"], "build")
        with self.assertRaises(ReleaseError):
            plan(service, service.tag, "push", "d" * 40)
        self.assertEqual(plan(service, service.tag, "workflow_dispatch")["mode"], "build")
        publish(service, service.identity, service.anchor)
        self.assertEqual(plan(service, service.tag, "workflow_dispatch")["mode"], "retry")

    def test_draft_lookup_survives_published_endpoint_404(self):
        service = Service()
        service.fail_upload = "release.json"
        with self.assertRaisesRegex(ReleaseError, "interrupted"):
            publish(service, service.identity, service.anchor)
        self.assertIsNone(service.request("GET", f"{PREFIX}/releases/tags/{service.tag}"))
        self.assertEqual(plan(service, service.tag, "workflow_dispatch")["mode"], "retry")
        service.fail_upload = None
        self.assertEqual(publish(service, service.identity)["status"], "published")
        self.assertEqual(service.mutations.count(("POST", f"{PREFIX}/releases")), 1)
        self.assertTrue(all(asset["download_count"] > 0 for asset in service.document["assets"]))

    def test_duplicate_drafts_fail_closed(self):
        service = Service()
        service.fail_upload = "release.json"
        with self.assertRaises(ReleaseError):
            publish(service, service.identity, service.anchor)
        request = service.request

        def duplicates(method, path, *args, **kwargs):
            if path.startswith(f"{PREFIX}/releases?"):
                return [copy.deepcopy(service.document), {**copy.deepcopy(service.document), "id": 301}]
            return request(method, path, *args, **kwargs)

        service.request = duplicates
        service.mutations.clear()
        with self.assertRaisesRegex(ReleaseError, "multiple releases"):
            publish(service, service.identity)
        self.assertEqual(service.mutations, [])

    def test_published_retry_noop_without_retained_artifact(self):
        service = Service()
        self.assertEqual(publish(service, service.identity, service.anchor)["status"], "published")
        service.expired = True
        service.mutations.clear()
        service.downloads.clear()
        self.assertEqual(publish(service, service.identity)["status"], "verified-no-op")
        self.assertEqual(service.mutations, [])
        self.assertFalse(any("/actions/artifacts/" in path for path in service.downloads))

    def test_partial_published_never_appends(self):
        service = Service()
        publish(service, service.identity, service.anchor)
        service.document["assets"].pop()
        service.mutations.clear()
        with self.assertRaisesRegex(ReleaseError, "incomplete"):
            publish(service, service.identity)
        self.assertEqual(service.mutations, [])

    def test_partial_draft_uses_original_bytes(self):
        service = Service()
        service.fail_upload = "git_guard-1.18.tar.gz"
        with self.assertRaisesRegex(ReleaseError, "interrupted"):
            publish(service, service.identity, service.anchor)
        self.assertTrue(service.document["draft"])
        original_ids = [a["id"] for a in service.document["assets"]]
        service.fail_upload = None
        publish(service, service.identity, {**service.anchor, "artifact_id": 999})
        self.assertEqual(original_ids, [a["id"] for a in service.document["assets"]][:2])
        self.assertFalse(service.document["draft"])
        self.assertEqual(len(service.document["assets"]), 4)

    def test_draft_without_manifest_recovers_original_artifact(self):
        service = Service()
        service.fail_upload = "release.json"
        with self.assertRaises(ReleaseError):
            publish(service, service.identity, service.anchor)
        self.assertEqual(service.document["assets"], [])
        service.fail_upload = None
        self.assertEqual(publish(service, service.identity)["status"], "published")

    def test_expired_draft_artifact_fails_without_rebuild(self):
        service = Service()
        service.fail_upload = "release.json"
        with self.assertRaises(ReleaseError):
            publish(service, service.identity, service.anchor)
        service.expired = True
        service.mutations.clear()
        with self.assertRaisesRegex(ReleaseError, "expired"):
            publish(service, service.identity)
        self.assertEqual(service.mutations, [])

    def test_tampered_bundle_and_zip_rejected(self):
        service = Service()
        files = dict(service.files)
        files["git_guard-1.18.tar.gz"] = b"substituted source"
        with self.assertRaisesRegex(ReleaseError, "artifact bytes"):
            validate_bundle(files, service.identity, service.anchor["manifest_sha256"])
        with self.assertRaisesRegex(ReleaseError, "expected SHA256"):
            validate_bundle(service.files, service.identity, "0" * 64)
        service.archive += b"tampered"
        with self.assertRaisesRegex(ReleaseError, "ZIP digest"):
            publish(service, service.identity, service.anchor)
        self.assertEqual(service.mutations, [])

    def test_tampered_published_bytes_and_anchor_rejected(self):
        service = Service()
        publish(service, service.identity, service.anchor)
        asset = next(a for a in service.document["assets"] if a["name"].endswith(".whl"))
        service.asset_bytes[asset["id"]] = b"changed wheel bytes!"
        with self.assertRaisesRegex(ReleaseError, "asset bytes"):
            verify_published_release(service, service.tag)
        service.document["body"] = "unanchored"
        with self.assertRaisesRegex(ReleaseError, "anchor"):
            verify_published_release(service, service.tag)

    def test_asset_metadata_identity_is_not_synthesized(self):
        for changes in ({"state": "starter"}, {"id": -1}, {"browser_download_url": "https://example.invalid/evil"}):
            service = Service()
            publish(service, service.identity, service.anchor)
            service.document["assets"][-1].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ReleaseError):
                verify_published_release(service, service.tag)

    def test_identity_rechecked_after_artifact_download_before_write(self):
        service = Service()
        download = service.download

        def moved(path):
            data = download(path)
            service.tag_object = "d" * 40
            return data

        service.download = moved
        with self.assertRaisesRegex(ReleaseError, "drifted"):
            publish(service, service.identity, service.anchor)
        self.assertEqual(service.mutations, [])

    def test_asset_redirect_never_leaks_api_authorization(self):
        request = urllib.request.Request("https://api.github.com/repos/owner/repo/releases/assets/1",
                                         headers={"Authorization": "Bearer test-secret"})
        redirected = _Redirect().redirect_request(request, None, 302, "Found", {},
                                                    "https://release-assets.githubusercontent.com/opaque")
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaisesRegex(ReleaseError, "non-HTTPS"):
            _Redirect().redirect_request(request, None, 302, "Found", {}, "http://example.invalid/asset")

    def test_actions_archive_and_release_asset_media_types(self):
        service = Service()
        github = GitHub("test-only-not-a-real-token")

        def endpoint(request, **kwargs):
            path = request.full_url.removeprefix("https://api.github.com")
            if path == f"{PREFIX}/actions/artifacts/100":
                media_type = "application/vnd.github+json"
                content = json.dumps(service.request("GET", path)).encode()
            elif path == f"{PREFIX}/actions/artifacts/100/zip":
                media_type = "application/vnd.github+json"
                content = service.archive
            elif path == f"{PREFIX}/releases/assets/400":
                media_type = "application/octet-stream"
                content = service.files["git_guard-1.18.tar.gz"]
            else:
                raise AssertionError(path)
            if request.get_header("Accept") != media_type:
                raise urllib.error.HTTPError(request.full_url, 415, "Unsupported media type", {}, None)
            return io.BytesIO(content)

        with patch.object(github._opener, "open", side_effect=endpoint):
            files = recover_artifact(github, service.anchor)
            manifest, raw = validate_bundle(files, service.identity, service.anchor["manifest_sha256"])
            downloaded = github.download(f"{PREFIX}/releases/assets/400")
        self.assertEqual(sha256(downloaded), manifest["assets"][0]["sha256"])
        self.assertEqual(sha256(raw), service.anchor["manifest_sha256"])

    def test_no_auth_or_http_failure_is_absence(self):
        for token in (None, "", " "):
            with self.assertRaisesRegex(ReleaseError, "explicit GitHub token"):
                GitHub(token)
        github = GitHub("test-only-not-a-real-token")
        for code in (401, 403, 500):
            with patch.object(github._opener, "open", side_effect=urllib.error.HTTPError("https://api.github.com", code, "failure", {}, None)):
                with self.assertRaisesRegex(ReleaseError, f"HTTP {code}"):
                    github.request("GET", "/anything", allow_404=True)
        with patch.object(github._opener, "open", side_effect=urllib.error.HTTPError("https://api.github.com", 404, "missing", {}, None)):
            self.assertIsNone(github.request("GET", "/anything", allow_404=True))
        with patch.object(github._opener, "open", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(ReleaseError, "network"):
                github.request("GET", "/anything", allow_404=True)


class LocalGitTests(unittest.TestCase):
    def test_real_git_static_validation_and_main_ancestry(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)

            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

            git("init", "-q", "-b", "main")
            git("config", "user.name", "Release Test")
            git("config", "user.email", "test@example.invalid")
            git("config", "commit.gpgsign", "false")
            git("remote", "add", "origin", f"https://github.com/{SOURCE_REPO}.git")
            (repo / "src").mkdir()
            (repo / "pyproject.toml").write_text('[project]\nversion="1.18"\n')
            (repo / "src/cli.py").write_text('__version__="1.18"\nraise RuntimeError("never import")\n')
            git("add", ".")
            git("commit", "-qm", "source")
            git("tag", "-a", "V1.18", "-m", "release")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            identity = validate_local_tag(repo, "V1.18")
            self.assertNotEqual(identity["tag_object_sha"], identity["commit_sha"])
            # Descendant source is allowed: release need not equal main's tip.
            (repo / "next").write_text("next main commit")
            git("add", ".")
            git("commit", "-qm", "next")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            self.assertEqual(validate_local_tag(repo, "V1.18"), identity)
            # The project's real GitHub remote is upstream-github, not origin.
            git("remote", "rename", "origin", "upstream-github")
            git("remote", "add", "origin", "ssh://git@example.invalid/team/internal.git")
            self.assertEqual(validate_local_tag(repo, "V1.18"), identity)
            git("tag", "-f", "V1.18")
            with self.assertRaisesRegex(ReleaseError, "moved"):
                validate_local_tag(repo, "V1.18", identity)
            git("update-ref", "refs/remotes/upstream-github/main", "HEAD~1")
            with self.assertRaisesRegex(ReleaseError, "identity validation"):
                validate_local_tag(repo, "V1.18")
            git("remote", "set-url", "upstream-github", "https://github.com/someone/other.git")
            with self.assertRaisesRegex(ReleaseError, "exact source"):
                validate_local_tag(repo, "V1.18")


if __name__ == "__main__":
    unittest.main()
