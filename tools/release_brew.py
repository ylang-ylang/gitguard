#!/usr/bin/env python3
"""Render the source-sdist Formula and distribute verified releases to our tap.

Only HOMEBREW_TAP_TOKEN may mutate the tap. GITHUB_TOKEN is source-read-only.
An existing deterministic branch is reusable only with our exact owned PR;
orphan/foreign branches fail closed rather than being reset or adopted.
"""
from __future__ import annotations

import argparse
import base64
import email.parser
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tarfile
import time
from urllib.parse import urlencode

from release_common import GitHub, ReleaseError, SOURCE_REPO, verify_published_release

TAP_REPO = "ylang-ylang/homebrew-tap"
FORMULA_PATH = "Formula/git-guard.rb"
REQUIRED_CHECK = "tap-checks"
ROOT = f"/repos/{TAP_REPO}"
TAG_RE = re.compile(r"V(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA_RE = re.compile(r"[0-9a-f]{40}")


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def version_key(version):
    require(isinstance(version, str) and TAG_RE.fullmatch("V" + version), "invalid version")
    return tuple(map(int, version.split(".")))


def token(name):
    value = os.environ.get(name, "")
    require(bool(value) and not any(c.isspace() for c in value), f"{name} must be configured explicitly")
    return value


def sha(value):
    require(isinstance(value, str) and SHA_RE.fullmatch(value), "invalid git object SHA")
    return value


def render_formula(version, url, digest, *, local=False):
    version_key(version)
    require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "invalid archive digest")
    expected = f"https://github.com/{SOURCE_REPO}/releases/download/V{version}/git_guard-{version}.tar.gz"
    if local:
        require(url.startswith("file:///") and not any(c in url for c in '\n\r"#'), "invalid local archive URL")
    else:
        require(url == expected, "archive URL does not match the verified release")
    comment = "# LOCAL VALIDATION ONLY: not a published release.\n" if local else ""
    return comment + f'''class GitGuard < Formula
  include Language::Python::Virtualenv

  desc "Enforce Git branch-flow policies from Mermaid documents"
  homepage "https://github.com/{SOURCE_REPO}"
  url "{url}"
  version "{version}"
  sha256 "{digest}"
  license "AGPL-3.0-only"

  depends_on "python-setuptools" => :build
  depends_on "python@3.13"
  depends_on "git"
  depends_on "bash"
  depends_on :linux
  depends_on arch: :x86_64

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_equal "git-guard #{{version}}", shell_output("#{{bin}}/git-guard --version").strip
    system "git", "init", "-b", "main", testpath
    system "git", "config", "user.name", "Homebrew Test"
    system "git", "config", "user.email", "test@example.invalid"
    system "git", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "initial"
    original = shell_output("git rev-parse HEAD").strip
    system "git", "branch", "dev"
    system bin/"git-guard", "install", "--repo", testpath, "--config", "dev-only"
    assert_path_exists testpath/".git-guard/policy.json"
    rejection = shell_output("git update-ref -d refs/heads/main 2>&1", 128)
    assert_match "PROTECTED_REF_DELETE", rejection
    assert_equal original, shell_output("git rev-parse refs/heads/main").strip
  end
end
'''


def render_local(archive, version):
    """Read real candidate bytes; never invent a digest or release asset URL."""
    version_key(version)
    archive = Path(archive).resolve(strict=True)
    require(archive.name == f"git_guard-{version}.tar.gz", "candidate sdist filename/version mismatch")
    with tarfile.open(archive, "r:gz") as source:
        members = [m for m in source.getmembers() if m.name == f"git_guard-{version}/PKG-INFO"]
        require(len(members) == 1 and members[0].isfile(), "candidate has no unique root PKG-INFO")
        require(members[0].size < 1024 * 1024, "candidate metadata is too large")
        metadata = email.parser.BytesParser().parsebytes(source.extractfile(members[0]).read())
        require(metadata["Name"] == "git-guard" and metadata["Version"] == version,
                "candidate package identity mismatch")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return render_formula(version, archive.as_uri(), digest, local=True)


def release_formula(spec):
    version_key(spec["version"])
    require(spec["tag"] == "V" + spec["version"], "release tag/version mismatch")
    assets = [a for a in spec["manifest"]["assets"] if a["kind"] == "sdist"]
    require(len(assets) == 1, "release must have exactly one source archive")
    asset = assets[0]
    return render_formula(spec["version"], asset["url"], asset["sha256"])


def pages(api, path, key=None):
    result = []
    for page in range(1, 101):
        separator = "&" if "?" in path else "?"
        response = api.request("GET", f"{path}{separator}per_page=100&page={page}")
        batch = response[key] if key else response
        require(isinstance(batch, list), "invalid paginated API response")
        result.extend(batch)
        if len(batch) < 100:
            if key and "total_count" in response:
                require(len(result) == response["total_count"], "incomplete paginated response")
            return result
    raise ReleaseError("API pagination exceeds safe bound")


def head(api, branch):
    result = api.request("GET", f"{ROOT}/git/ref/heads/{branch}", allow_404=True)
    return sha(result["object"]["sha"]) if result else None


def formula_at(api, commit):
    response = api.request("GET", f"{ROOT}/contents/{FORMULA_PATH}?ref={sha(commit)}", allow_404=True)
    if response is None:
        return None
    require(response.get("type") == "file" and response.get("path") == FORMULA_PATH
            and response.get("encoding") == "base64", "Formula is not a regular file")
    return base64.b64decode(response["content"]).decode("utf-8")


def update_needed(current, target, version):
    if current is None:
        return True
    matches = re.findall(r'^  version "([^"\n]+)"$', current, re.MULTILINE)
    require(len(matches) == 1, "existing Formula has ambiguous version")
    old, new = version_key(matches[0]), version_key(version)
    require(old <= new, "refusing a Formula downgrade")
    if old == new:
        require(current == target, "published Formula version has a different identity or contents")
        return False
    return True


def ownership(spec, actor_id):
    fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"git-guard release {spec['tag']}\n\nsource: {SOURCE_REPO}\nrelease-identity: {fingerprint}\ntap-actor: {actor_id}\n"


def exact_diff(files):
    require(len(files) == 1 and files[0].get("filename") == FORMULA_PATH
            and files[0].get("status") in {"added", "modified"}
            and not files[0].get("previous_filename"), "PR must change exactly Formula/git-guard.rb")


def validate_pull(pull, *, branch, expected_head, body, actor_id):
    require(type(pull.get("number")) is int and pull["number"] > 0, "invalid PR number")
    require(pull.get("user", {}).get("id") == actor_id and pull.get("body") == body,
            "refusing to adopt foreign PR or changed release identity")
    for side, ref in (("base", "main"), ("head", branch)):
        require(pull.get(side, {}).get("ref") == ref
                and pull[side].get("repo", {}).get("full_name") == TAP_REPO,
                "PR repository or branch identity changed")
    require(pull["head"].get("sha") == expected_head, "PR head identity changed")
    require(pull.get("state") == "open" and not pull.get("merged_at") and not pull.get("draft"),
            "distribution PR is not open and ready")
    require(pull.get("mergeable") is not False, "distribution PR conflicts with tap main")


def validate_pr_snapshot(api, number, branch, commit, body, actor_id, target):
    require(head(api, branch) == commit, "distribution branch head changed")
    pull = api.request("GET", f"{ROOT}/pulls/{number}")
    validate_pull(pull, branch=branch, expected_head=commit, body=body, actor_id=actor_id)
    files = pages(api, f"{ROOT}/pulls/{number}/files")
    require(pull.get("changed_files") == len(files), "PR diff is incomplete")
    exact_diff(files)
    require(formula_at(api, commit) == target, "PR Formula contents changed")
    # Detect a push while fetching the paginated diff.
    require(head(api, branch) == commit, "distribution branch changed during diff validation")


def checks_ready(runs, statuses, commit):
    required = [run for run in runs if run.get("name") == REQUIRED_CHECK]
    for run in runs:
        require(run.get("head_sha") == commit, "check belongs to a different head SHA")
        if run.get("status") == "completed":
            allowed = {"success"} if run.get("name") == REQUIRED_CHECK else {"success", "neutral", "skipped"}
            require(run.get("conclusion") in allowed, "tap check failed or required check did not succeed")
    latest = {}
    for status in statuses:
        require(type(status.get("id")) is int and isinstance(status.get("context"), str), "invalid status identity")
        context = status["context"]
        if status["id"] > latest.get(context, {}).get("id", -1):
            latest[context] = status
    for status in latest.values():
        require(status.get("state") in {"success", "pending"}, "tap commit status failed")
    return bool(required) and all(r.get("status") == "completed" for r in runs) and all(
        s["state"] == "success" for s in latest.values())


def wait_checks(api, commit, *, timeout, poll, sleep=time.sleep, now=time.monotonic):
    require(math.isfinite(timeout) and timeout >= 0 and math.isfinite(poll) and poll > 0,
            "invalid check wait interval")
    deadline = now() + timeout
    while True:
        runs = pages(api, f"{ROOT}/commits/{commit}/check-runs?filter=latest", "check_runs")
        statuses = pages(api, f"{ROOT}/commits/{commit}/statuses")
        if checks_ready(runs, statuses, commit):
            return
        require(now() < deadline, "timed out waiting for explicit tap-checks success")
        sleep(poll)


def update_tap(source, tap, tag, *, timeout=900, poll=10, verify=verify_published_release,
               sleep=time.sleep, now=time.monotonic):
    require(isinstance(tag, str) and TAG_RE.fullmatch(tag), "invalid release tag")
    spec = verify(source, tag)
    target = release_formula(spec)
    actor = tap.request("GET", "/user")
    require(type(actor.get("id")) is int and actor["id"] > 0, "tap token has no authenticated user identity")
    actor_id = actor["id"]
    require(tap.request("GET", ROOT).get("default_branch") == "main", "tap default branch changed")
    base = head(tap, "main")
    require(base is not None, "tap main is missing")
    original = formula_at(tap, base)
    if not update_needed(original, target, spec["version"]):
        require(verify(source, tag) == spec, "source release identity changed")
        return "already-current"
    branch = f"brew/git-guard-{tag}"
    body = ownership(spec, actor_id)
    query = urlencode({"head": f"{TAP_REPO.split('/')[0]}:{branch}", "state": "all"})
    pulls = pages(tap, f"{ROOT}/pulls?{query}")
    commit = head(tap, branch)
    if commit is not None:
        require(len(pulls) == 1, "existing branch has no unique owned PR; refusing adoption")
        pull = pulls[0]
        validate_pull(pull, branch=branch, expected_head=commit, body=body, actor_id=actor_id)
        number = pull["number"]
    else:
        require(not pulls, "deterministic PR already exists without its original branch")
        # Build one commit from the captured main tree, then create its ref atomically.
        # Never PUT a moving branch or force-update an existing ref.
        base_commit = tap.request("GET", f"{ROOT}/git/commits/{base}")
        blob = tap.request("POST", f"{ROOT}/git/blobs", {"content": target, "encoding": "utf-8"})
        tree = tap.request("POST", f"{ROOT}/git/trees", {
            "base_tree": sha(base_commit["tree"]["sha"]),
            "tree": [{"path": FORMULA_PATH, "mode": "100644", "type": "blob", "sha": sha(blob["sha"])}],
        })
        created = tap.request("POST", f"{ROOT}/git/commits", {
            "message": body, "tree": sha(tree["sha"]), "parents": [base],
        })
        commit = sha(created["sha"])
        tap.request("POST", f"{ROOT}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit})
        pull = tap.request("POST", f"{ROOT}/pulls", {
            "title": f"git-guard {spec['version']}", "head": branch, "base": "main", "body": body,
        })
        number = pull["number"]
    commit_info = tap.request("GET", f"{ROOT}/git/commits/{commit}")
    require(commit_info.get("message") == body and len(commit_info.get("parents", [])) == 1,
            "distribution commit ownership changed")
    validate_pr_snapshot(tap, number, branch, commit, body, actor_id, target)
    wait_checks(tap, commit, timeout=timeout, poll=poll, sleep=sleep, now=now)
    require(verify(source, tag) == spec, "source release identity changed before merge")
    current = formula_at(tap, head(tap, "main"))
    if not update_needed(current, target, spec["version"]):
        return "already-current"
    require(current == original, "tap Formula changed concurrently; refusing overwrite")
    validate_pr_snapshot(tap, number, branch, commit, body, actor_id, target)
    # Check again after the expensive source verification: reruns/pending checks block.
    wait_checks(tap, commit, timeout=0, poll=poll, sleep=sleep, now=now)
    merged = tap.request("PUT", f"{ROOT}/pulls/{number}/merge", {"sha": commit, "merge_method": "merge"})
    require(merged.get("merged") is True, "tap PR merge did not succeed")
    require(formula_at(tap, head(tap, "main")) == target, "merged tap Formula differs from verified release")
    return "merged"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("render-local", help="render actual local sdist bytes for Homebrew validation only")
    local.add_argument("--sdist", required=True, type=Path)
    local.add_argument("--version", required=True)
    local.add_argument("--output", required=True, type=Path)
    update = commands.add_parser("update-tap")
    update.add_argument("--tag", required=True)
    update.add_argument("--check-timeout", type=float, default=900)
    args = parser.parse_args(argv)
    if args.command == "render-local":
        text = render_local(args.sdist, args.version)
        args.output.write_text(text, encoding="utf-8")
    else:
        # Load independently: never substitute the tap credential for source access.
        source_token, tap_token = token("GITHUB_TOKEN"), token("HOMEBREW_TAP_TOKEN")
        result = update_tap(GitHub(source_token), GitHub(tap_token), args.tag, timeout=args.check_timeout)
        print(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"release-brew: {exc}", file=sys.stderr)
        raise SystemExit(1)
