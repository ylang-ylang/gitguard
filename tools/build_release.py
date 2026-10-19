#!/usr/bin/env python3
"""Build setuptools artifacts; only explicit, exact-HEAD identity is releasable."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

SOURCE_REPOSITORY = "ylang-ylang/gitguard"
TAG_PATTERN = re.compile(r"V(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def run(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def source_version(root: Path) -> str:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    assignments = []
    for node in ast.parse((root / "src/cli.py").read_text(encoding="utf-8")).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            assignments.append(node.value.value if isinstance(node.value, ast.Constant) else None)
    if assignments != [version] or not isinstance(version, str) or not TAG_PATTERN.fullmatch("V" + version):
        raise ValueError("pyproject version and single literal CLI __version__ must match a V<major>.<minor> tag")
    return version


def extract_archive(archive: tarfile.TarFile, destination: Path) -> None:
    # No links or special files are needed by this pure-Python distribution.
    for member in archive.getmembers():
        target = destination / member.name
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError(f"unsafe archive path: {member.name}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"unreadable archive member: {member.name}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
        else:
            raise ValueError(f"unsupported archive member: {member.name}")


def build(repo_root: Path, out_dir: Path, tag: str | None, commit: str | None) -> dict:
    repo_root = repo_root.resolve()
    out_dir = out_dir.resolve()
    if bool(tag) != bool(commit):
        raise ValueError("--tag and --commit must be supplied together")
    if tag:
        if not TAG_PATTERN.fullmatch(tag) or not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
            raise ValueError("release identity requires V<major>.<minor> and a full lowercase 40-hex commit")
        if run("git", "rev-parse", "HEAD", cwd=repo_root) != commit:
            raise ValueError("release commit is not the exact source HEAD")
        if run("git", "diff", "--name-only", "HEAD", "--", cwd=repo_root):
            raise ValueError("release source has tracked modifications")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("output directory must be empty; never overwrite release artifacts")
    with tempfile.TemporaryDirectory(prefix="git-guard-build-") as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir()
        if tag:
            archived = subprocess.run(
                ["git", "archive", "--format=tar", commit], cwd=repo_root,
                check=True, stdout=subprocess.PIPE,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
                extract_archive(archive, stage)
        else:
            for name in ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE", "NOTICE", "src", "configs"):
                source = repo_root / name
                if source.is_dir():
                    shutil.copytree(source, stage / name, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "*.pyc"))
                else:
                    shutil.copy2(source, stage / name)
        version = source_version(stage)
        if tag and tag != "V" + version:
            raise ValueError("tag does not match source metadata and literal CLI version")
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "build", "--sdist", "--outdir", str(out_dir), str(stage)], check=True)
        sdist_name = f"git_guard-{version}.tar.gz"
        sdist = out_dir / sdist_name
        if not sdist.is_file():
            raise ValueError(f"setuptools did not produce canonical archive {sdist_name}")
        unpacked = Path(temporary) / "sdist"
        unpacked.mkdir()
        with tarfile.open(sdist) as archive:
            extract_archive(archive, unpacked)
        source = unpacked / f"git_guard-{version}"
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir), str(source)], check=True)
        wheel_name = f"git_guard-{version}-py3-none-any.whl"
        if sorted(path.name for path in out_dir.iterdir()) != sorted([sdist_name, wheel_name]):
            raise ValueError("build produced unexpected artifacts")
        assets = []
        for filename, kind in ((sdist_name, "sdist"), (wheel_name, "wheel")):
            path = out_dir / filename
            with path.open("rb") as content:
                digest = hashlib.file_digest(content, "sha256").hexdigest()
            asset = {"filename": filename, "kind": kind, "sha256": digest, "size": path.stat().st_size}
            if tag:
                asset["url"] = f"https://github.com/{SOURCE_REPOSITORY}/releases/download/{tag}/{filename}"
            assets.append(asset)
        manifest = {
            "schema_version": 1,
            "version": version,
            "source": {"repository": SOURCE_REPOSITORY, "tag": tag, "commit": commit},
            "assets": assets,
        }
        if not tag:
            manifest["candidate"] = True
        (out_dir / "release.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (out_dir / "SHA256SUMS").write_text("".join(f"{item['sha256']}  {item['filename']}\n" for item in assets), encoding="utf-8")
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tag")
    parser.add_argument("--commit")
    args = parser.parse_args()
    try:
        manifest = build(args.repo_root, args.out_dir, args.tag, args.commit)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"build failed: {exc}\n")
    print(f"built {'release' if args.tag else 'LOCAL CANDIDATE (not releasable)'} {manifest['version']} in {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
