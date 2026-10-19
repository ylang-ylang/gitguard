"""Standard-library, non-executing GitHub release identity and integrity boundary."""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request

SOURCE_REPO = "ylang-ylang/gitguard"
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
TAG = re.compile(r"V(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
PREFIX = f"/repos/{SOURCE_REPO}"


class ReleaseError(RuntimeError):
    """Fail closed: an identity, integrity, authentication or service failure."""


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise ReleaseError("refusing non-HTTPS download redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlparse(newurl).netloc != "api.github.com":
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, token=None):
        if not token or not token.strip():
            raise ReleaseError("explicit GitHub token required; no credential fallback")
        self._token = token
        self._opener = urllib.request.build_opener(_Redirect())

    def _send(self, method, path, data=None, *, binary=False, allow_404=False):
        url = "https://api.github.com" + path if path.startswith("/") else path
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {"api.github.com", "uploads.github.com"}:
            raise ReleaseError("GitHub request must use the fixed HTTPS API/upload host")
        # Actions archive endpoints accept the JSON API media type and redirect
        # to ZIP bytes. Only Release asset downloads require octet-stream.
        release_asset = binary and re.fullmatch(r"/repos/[^/]+/[^/]+/releases/assets/[0-9]+", parsed.path)
        headers = {"Authorization": f"Bearer {self._token}", "X-GitHub-Api-Version": "2022-11-28",
                   "Accept": "application/octet-stream" if release_asset else "application/vnd.github+json"}
        if isinstance(data, bytes):
            headers["Content-Type"] = "application/octet-stream"
        elif data is not None:
            data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        try:
            with self._opener.open(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=60) as response:
                body = response.read()
            return body if binary else (json.loads(body) if body else None)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_404:
                return None
            raise ReleaseError(f"GitHub {method} failed: HTTP {exc.code}; not an absent release") from exc
        except (OSError, ValueError) as exc:
            raise ReleaseError(f"GitHub {method} network/response failure: {type(exc).__name__}") from exc

    def request(self, method, path, data=None, *, allow_404=False):
        return self._send(method, path, data, allow_404=allow_404)

    def download(self, path):
        return self._send("GET", path, binary=True)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def version_for_tag(tag):
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        raise ReleaseError("tag must be exactly V<major>.<minor>, without leading zeroes")
    return tag[1:]


def validate_versions(pyproject, cli, version):
    try:
        metadata = tomllib.loads(pyproject)["project"]["version"]
        tree = ast.parse(cli)
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
        if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Constant):
            raise ValueError("CLI version must be one literal assignment")
        values = [assignments[0].value.value]
    except (ValueError, SyntaxError, KeyError, TypeError) as exc:
        raise ReleaseError("source versions are not readable static TOML/AST literals") from exc
    if metadata != version or values != [version]:
        raise ReleaseError(f"source versions must both equal {version!r}")


def validate_remote_tag(github, tag, expected=None):
    version = version_for_tag(tag)
    ref = github.request("GET", f"{PREFIX}/git/ref/tags/{tag}")
    if ref.get("ref") != f"refs/tags/{tag}":
        raise ReleaseError("tag reference identity mismatch")
    obj = ref["object"]
    tag_object = obj["sha"]
    for _ in range(16):
        if not SHA.fullmatch(obj.get("sha", "")):
            raise ReleaseError("tag object is not a full SHA")
        if obj["type"] == "commit":
            break
        if obj["type"] != "tag":
            raise ReleaseError("tag does not peel to a commit")
        obj = github.request("GET", f"{PREFIX}/git/tags/{obj['sha']}")["object"]
    else:
        raise ReleaseError("excessive annotated tag nesting")
    commit = obj["sha"]
    main = github.request("GET", f"{PREFIX}/git/ref/heads/main")["object"]["sha"]
    comparison = github.request("GET", f"{PREFIX}/compare/{commit}...{main}")
    if comparison.get("status") not in {"ahead", "identical"} or comparison.get("merge_base_commit", {}).get("sha") != commit:
        raise ReleaseError("tag commit is not in remote main history")
    files = []
    for path in ("pyproject.toml", "src/cli.py"):
        document = github.request("GET", f"{PREFIX}/contents/{path}?ref={commit}")
        if document.get("type") != "file" or document.get("encoding") != "base64":
            raise ReleaseError("source metadata must be regular base64 files")
        files.append(base64.b64decode(document["content"]).decode("utf-8"))
    validate_versions(*files, version)
    identity = dict(tag=tag, version=version, tag_object_sha=tag_object, commit_sha=commit)
    if expected is not None and any(expected.get(key) != value for key, value in identity.items()):
        raise ReleaseError("tag identity drifted since validation")
    return identity


def release_document(github, tag):
    version_for_tag(tag)
    result = github.request("GET", f"{PREFIX}/releases/tags/{tag}", allow_404=True)
    if result is None:
        # The by-tag endpoint exposes published releases only. A 404 is not
        # proof of absence until the authenticated draft listing is complete.
        matches = []
        for page in range(1, 101):
            batch = github.request("GET", f"{PREFIX}/releases?per_page=100&page={page}")
            if not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch):
                raise ReleaseError("invalid release listing")
            matches.extend(item for item in batch if item.get("tag_name") == tag)
            if len(batch) < 100:
                break
        else:
            raise ReleaseError("release listing exceeds pagination safety bound")
        if len(matches) > 1:
            raise ReleaseError("multiple releases share the requested tag; refusing draft adoption")
        result = matches[0] if matches else None
    if result is not None and (result.get("tag_name") != tag or result.get("prerelease")):
        raise ReleaseError("release tag/prerelease identity mismatch")
    if result is not None and (type(result.get("id")) is not int or result["id"] <= 0 or type(result.get("draft")) is not bool):
        raise ReleaseError("release has invalid id/draft metadata")
    return result


def parse_anchor(document, identity):
    matches = re.findall(r"^<!-- git-guard-release:(.+) -->$", document.get("body") or "", re.M)
    if len(matches) != 1:
        raise ReleaseError("release lacks a unique immutable manifest anchor")
    try:
        anchor = json.loads(matches[0])
    except ValueError as exc:
        raise ReleaseError("invalid release anchor") from exc
    if any(anchor.get(key) != value for key, value in identity.items()):
        raise ReleaseError("release anchor tag identity drift")
    if not DIGEST.fullmatch(anchor.get("manifest_sha256", "")) or not DIGEST.fullmatch(anchor.get("artifact_sha256", "")):
        raise ReleaseError("release lacks immutable manifest/artifact digest")
    for key in ("artifact_id", "run_id", "run_attempt"):
        if type(anchor.get(key)) is not int or anchor[key] <= 0:
            raise ReleaseError(f"release anchor lacks original {key}")
    return anchor


def validate_manifest(raw, identity):
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise ReleaseError("invalid manifest JSON") from exc
    version = identity["version"]
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("version") != version or manifest.get("candidate"):
        raise ReleaseError("invalid release manifest schema/version")
    if manifest.get("source") != {"repository": SOURCE_REPO, "tag": identity["tag"], "commit": identity["commit_sha"]}:
        raise ReleaseError("manifest source identity mismatch")
    assets = manifest.get("assets")
    expected = [(f"git_guard-{version}.tar.gz", "sdist"), (f"git_guard-{version}-py3-none-any.whl", "wheel")]
    if not isinstance(assets, list) or len(assets) != 2:
        raise ReleaseError("manifest must contain exactly sdist and wheel")
    for asset, (name, kind) in zip(assets, expected):
        if not isinstance(asset, dict) or asset.get("filename") != name or asset.get("kind") != kind:
            raise ReleaseError("noncanonical release asset")
        if not DIGEST.fullmatch(asset.get("sha256", "")) or type(asset.get("size")) is not int or asset["size"] <= 0:
            raise ReleaseError("invalid asset digest/size")
        if asset.get("url") != f"https://github.com/{SOURCE_REPO}/releases/download/{identity['tag']}/{name}":
            raise ReleaseError("asset URL is not the fixed release URL")
    return manifest


def expected_files(manifest, raw):
    checksums = "".join(f"{a['sha256']}  {a['filename']}\n" for a in manifest["assets"]).encode()
    expected = {a["filename"]: (a["sha256"], a["size"]) for a in manifest["assets"]}
    expected.update({"release.json": (sha256(raw), len(raw)), "SHA256SUMS": (sha256(checksums), len(checksums))})
    return expected


def verify_assets(github, document, manifest, raw, *, complete):
    expected = expected_files(manifest, raw)
    assets = document.get("assets", [])
    names = [a.get("name") for a in assets]
    if len(names) != len(set(names)) or set(names) - set(expected):
        raise ReleaseError("duplicate or unexpected release assets")
    ids = [a.get("id") for a in assets]
    if any(type(value) is not int or value <= 0 for value in ids) or len(ids) != len(set(ids)):
        raise ReleaseError("release asset ids must be positive and unique")
    verified = []
    for asset in assets:
        name = asset["name"]
        url = f"https://github.com/{SOURCE_REPO}/releases/download/{manifest['source']['tag']}/{name}"
        if asset.get("state") != "uploaded" or asset.get("browser_download_url") != url:
            raise ReleaseError(f"asset state/download URL identity mismatch: {name}")
        digest, size = expected[name]
        data = github.download(f"{PREFIX}/releases/assets/{asset['id']}")
        if asset.get("size") != size or len(data) != size or sha256(data) != digest:
            raise ReleaseError(f"published asset bytes differ from immutable anchor: {name}")
        if asset.get("digest") not in (None, f"sha256:{digest}"):
            raise ReleaseError(f"GitHub asset digest mismatch: {name}")
        verified.append(dict(id=asset["id"], name=name, size=size, sha256=digest, url=url))
    if complete and set(names) != set(expected):
        raise ReleaseError("published release is incomplete; never append, rebuild or replace")
    return sorted(verified, key=lambda a: a["name"])


def verify_published_release(github, tag):
    identity = validate_remote_tag(github, tag)
    document = release_document(github, tag)
    if document is None or document.get("draft"):
        raise ReleaseError("distribution requires a complete published release")
    anchor = parse_anchor(document, identity)
    matches = [a for a in document.get("assets", []) if a.get("name") == "release.json"]
    if len(matches) != 1:
        raise ReleaseError("published release is missing its unique manifest")
    raw = github.download(f"{PREFIX}/releases/assets/{matches[0]['id']}")
    if sha256(raw) != anchor["manifest_sha256"]:
        raise ReleaseError("manifest differs from release-note digest anchor")
    manifest = validate_manifest(raw, identity)
    assets = verify_assets(github, document, manifest, raw, complete=True)
    validate_remote_tag(github, tag, identity)
    return {**identity, "release_id": document["id"], "manifest_sha256": sha256(raw), "manifest": manifest, "assets": assets}
