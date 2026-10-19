#!/usr/bin/env python3
"""Trusted release orchestration; never imports or executes tagged source.

Local validate-tag --repo-root uses the cached main ref of the exact GitHub remote.
Production uses fresh fixed-repository REST identity checks before every mutation.
Draft recovery uses the original immutable Actions artifact, never a rebuild.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

from release_common import (DIGEST, PREFIX, SHA, SOURCE_REPO, GitHub, ReleaseError,
                            expected_files, parse_anchor, release_document, sha256,
                            validate_manifest, validate_remote_tag, validate_versions,
                            verify_assets, verify_published_release, version_for_tag)


def validate_local_tag(repo, tag, expected=None):
    version = version_for_tag(tag)

    def git(*args):
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if result.returncode:
            raise ReleaseError(f"git identity validation failed: {' '.join(args)}")
        return result.stdout.strip()

    allowed_urls = {f"https://github.com/{SOURCE_REPO}.git", f"https://github.com/{SOURCE_REPO}", f"git@github.com:{SOURCE_REPO}.git"}
    remotes = [name for name in git("remote").splitlines() if git("remote", "get-url", name) in allowed_urls]
    if len(remotes) != 1:
        raise ReleaseError("exact source repository must have one unambiguous GitHub remote")
    obj = git("rev-parse", "--verify", f"refs/tags/{tag}")
    commit = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if not SHA.fullmatch(obj) or not SHA.fullmatch(commit):
        raise ReleaseError("tag must bind full object and commit SHAs")
    git("merge-base", "--is-ancestor", commit, f"refs/remotes/{remotes[0]}/main")
    validate_versions(git("show", f"{commit}:pyproject.toml"), git("show", f"{commit}:src/cli.py"), version)
    identity = dict(tag=tag, version=version, tag_object_sha=obj, commit_sha=commit)
    if expected is not None and identity != expected:
        raise ReleaseError("local tag identity moved")
    return identity


def plan(github, tag, event, event_sha=None):
    if event not in {"push", "workflow_dispatch"}:
        raise ReleaseError("only tag pushes and explicit existing-tag retries are allowed")
    identity = validate_remote_tag(github, tag)
    if event == "push" and event_sha not in {identity["commit_sha"], identity["tag_object_sha"]}:
        raise ReleaseError("push event SHA differs from tag identity")
    document = release_document(github, tag)
    if document is not None:
        parse_anchor(document, identity)
        if not document.get("draft"):
            verify_published_release(github, tag)
    return {**identity, "mode": "build" if document is None else "retry"}


def validate_bundle(files, identity, manifest_digest):
    if not DIGEST.fullmatch(manifest_digest or ""):
        raise ReleaseError("an explicit trusted manifest SHA256 is required")
    raw = files.get("release.json")
    if raw is None or sha256(raw) != manifest_digest:
        raise ReleaseError("artifact manifest is not bound to the expected SHA256")
    manifest = validate_manifest(raw, identity)
    expected = expected_files(manifest, raw)
    if set(files) != set(expected):
        raise ReleaseError("artifact must contain exactly the four release files")
    for name, (digest, size) in expected.items():
        if len(files[name]) != size or sha256(files[name]) != digest:
            raise ReleaseError(f"artifact bytes differ from manifest: {name}")
    return manifest, raw


def local_bundle(directory):
    files = {}
    for path in Path(directory).iterdir():
        if path.is_symlink() or not path.is_file():
            raise ReleaseError("release bundle accepts regular files only")
        files[path.name] = path.read_bytes()
    return files


def recover_artifact(github, anchor):
    artifact_id = anchor["artifact_id"]
    document = github.request("GET", f"{PREFIX}/actions/artifacts/{artifact_id}")
    if document.get("id") != artifact_id or document.get("expired") or document.get("workflow_run", {}).get("id") != anchor["run_id"]:
        raise ReleaseError("original artifact unavailable/expired or belongs to another run; never rebuild")
    if document.get("name") != f"release-assets-{anchor['run_id']}-{anchor['run_attempt']}":
        raise ReleaseError("artifact run attempt/name identity mismatch")
    archive = github.download(f"{PREFIX}/actions/artifacts/{artifact_id}/zip")
    if sha256(archive) != anchor["artifact_sha256"]:
        raise ReleaseError("original artifact ZIP digest mismatch")
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            for entry in bundle.infolist():
                name = entry.filename
                if name in files or Path(name).name != name or name in {".", ".."} or stat.S_ISLNK(entry.external_attr >> 16) or entry.is_dir():
                    raise ReleaseError("unsafe/duplicate artifact ZIP member")
                files[name] = bundle.read(entry)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise ReleaseError("invalid retained artifact ZIP") from exc
    return files


def _same_release(github, identity, previous):
    validate_remote_tag(github, identity["tag"], identity)
    fresh = release_document(github, identity["tag"])
    if previous is None:
        if fresh is not None:
            raise ReleaseError("release appeared concurrently; retry without rebuilding")
    elif fresh is None or any(fresh.get(k) != previous.get(k) for k in ("id", "tag_name", "draft", "body", "assets")):
        raise ReleaseError("release identity/assets changed concurrently")
    return fresh


def publish(github, identity, build_anchor=None):
    tag = identity["tag"]
    validate_remote_tag(github, tag, identity)
    document = release_document(github, tag)
    if document is not None and not document.get("draft"):
        spec = verify_published_release(github, tag)
        if any(spec[key] != identity[key] for key in identity):
            raise ReleaseError("published release identity drift")
        return {"status": "verified-no-op", **spec}
    anchor = parse_anchor(document, identity) if document is not None else build_anchor
    if anchor is None:
        raise ReleaseError("first publication requires the original successful build artifact")
    # Validate the anchor identically for a new draft and a retained draft.
    body = "<!-- git-guard-release:" + json.dumps(anchor, sort_keys=True, separators=(",", ":")) + " -->"
    parse_anchor({"body": body}, identity)
    files = recover_artifact(github, anchor)
    manifest, raw = validate_bundle(files, identity, anchor["manifest_sha256"])
    if document is None:
        _same_release(github, identity, None)
        document = github.request("POST", f"{PREFIX}/releases", {
            "tag_name": tag, "target_commitish": identity["commit_sha"], "name": f"git-guard {identity['version']}",
            "body": f"git-guard {identity['version']}\n\n{body}\n", "draft": True, "prerelease": False})
        if not document.get("draft") or document.get("tag_name") != tag:
            raise ReleaseError("create response was not the requested draft")
    parse_anchor(document, identity)
    verify_assets(github, document, manifest, raw, complete=False)
    # Upload only absent ORIGINAL files; existing bytes are checked, never replaced.
    for name in ("release.json", "SHA256SUMS", *(a["filename"] for a in manifest["assets"])):
        if any(a["name"] == name for a in document.get("assets", [])):
            continue
        _same_release(github, identity, document)
        github.request("POST", f"https://uploads.github.com{PREFIX}/releases/{document['id']}/assets?name={name}", files[name])
        fresh = release_document(github, tag)
        if fresh is None or fresh["id"] != document["id"] or fresh.get("body") != document.get("body") or not fresh.get("draft"):
            raise ReleaseError("draft identity changed during asset upload")
        document = fresh
        verify_assets(github, document, manifest, raw, complete=False)
    verify_assets(github, document, manifest, raw, complete=True)
    _same_release(github, identity, document)
    github.request("PATCH", f"{PREFIX}/releases/{document['id']}", {"draft": False})
    return {"status": "published", **verify_published_release(github, tag)}


def emit(result, github_output=False):
    print(json.dumps(result, indent=2))
    if github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in result.items():
                encoded = str(value) if isinstance(value, (str, int)) else json.dumps(value, separators=(",", ":"))
                if "\n" in encoded or "\r" in encoded:
                    raise ReleaseError("unsafe multiline workflow output")
                output.write(f"{key}={encoded}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    validate = subs.add_parser("validate-tag", help="read-only static validation; --repo-root uses the cached GitHub remote main")
    validate.add_argument("--tag", required=True)
    validate.add_argument("--repo-root", type=Path)
    planner = subs.add_parser("plan")
    planner.add_argument("--tag", required=True)
    planner.add_argument("--event", required=True)
    planner.add_argument("--event-sha")
    planner.add_argument("--github-output", action="store_true")
    check = subs.add_parser("check-bundle", help="local read-only SHA-bound artifact validation; never publishes")
    check.add_argument("--directory", required=True, type=Path)
    check.add_argument("--identity", required=True, help="JSON identity from validate-tag")
    check.add_argument("--manifest-sha", required=True)
    publication = subs.add_parser("publish")
    publication.add_argument("--identity", required=True)
    publication.add_argument("--artifact-id", type=int)
    publication.add_argument("--artifact-sha")
    publication.add_argument("--manifest-sha")
    publication.add_argument("--run-id", type=int)
    publication.add_argument("--run-attempt", type=int)
    args = parser.parse_args()
    try:
        if args.command == "validate-tag":
            result = validate_local_tag(args.repo_root, args.tag) if args.repo_root else validate_remote_tag(GitHub(os.environ.get("GITHUB_TOKEN")), args.tag)
        elif args.command == "check-bundle":
            manifest, raw = validate_bundle(local_bundle(args.directory), json.loads(args.identity), args.manifest_sha)
            result = {"status": "valid", "manifest_sha256": sha256(raw), "version": manifest["version"]}
        else:
            github = GitHub(os.environ.get("GITHUB_TOKEN"))
            if args.command == "plan":
                result = plan(github, args.tag, args.event, args.event_sha)
                result["identity"] = {key: result[key] for key in ("tag", "version", "tag_object_sha", "commit_sha")}
            else:
                identity = json.loads(args.identity)
                anchor = None if args.artifact_id is None else {**identity, "artifact_id": args.artifact_id,
                    "artifact_sha256": args.artifact_sha, "manifest_sha256": args.manifest_sha,
                    "run_id": args.run_id, "run_attempt": args.run_attempt}
                result = publish(github, identity, anchor)
        emit(result, getattr(args, "github_output", False))
        return 0
    except (ReleaseError, ValueError, KeyError, OSError) as exc:
        print(f"release refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
