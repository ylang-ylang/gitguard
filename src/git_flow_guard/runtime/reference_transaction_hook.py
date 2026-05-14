#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ZERO = "0" * 40


@dataclass(frozen=True)
class RefUpdate:
    old: str
    new: str
    ref: str


@dataclass(frozen=True)
class SourceCandidate:
    ref: str
    sha: str
    rule: dict[str, Any]


class HookReject(RuntimeError):
    pass


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: reference_transaction_hook.py <phase>", file=sys.stderr)
        return 2

    phase = sys.argv[1]
    repo = Path(required_env("GFG_REPO_PATH"))
    policy = json.loads(Path(required_env("GFG_POLICY_JSON")).read_text(encoding="utf-8"))
    state_path = Path(os.environ.get("GFG_STATE_JSON", repo / ".git" / "gfg-state.json"))
    log_path = os.environ.get("GFG_LOG_PATH")
    updates = read_updates(sys.stdin)

    if log_path:
        append_log(Path(log_path), phase, updates)

    try:
        if phase == "prepared":
            validate_prepared(repo, policy, state_path, updates)
        elif phase == "committed":
            update_committed_state(repo, policy, state_path, updates)
        elif phase == "aborted":
            return 0
        else:
            raise HookReject(f"unsupported reference-transaction phase: {phase}")
    except HookReject as exc:
        print(f"git-flow-guard: {exc}", file=sys.stderr)
        return 1

    return 0


def validate_prepared(repo: Path, policy: dict[str, Any], state_path: Path, updates: list[RefUpdate]) -> None:
    pending = load_state(state_path).get("pending", {})
    proposed = {update.ref: update.new for update in updates if update.new != ZERO}

    for update in updates:
        if not is_policy_ref(update.ref):
            continue

        if update.ref.startswith("refs/heads/"):
            validate_branch_name(policy, update.ref)

        enforce_pending_lock(repo, pending, update)

        if update.ref.startswith("refs/tags/"):
            validate_tag(repo, policy, proposed, update)
            continue

        if update.ref.startswith("refs/heads/") and update.old == ZERO:
            validate_branch_creation(repo, policy, update)
            continue

        if update.ref in set(policy.get("protected_refs", [])):
            validate_protected_target_update(repo, policy, proposed, update)


def validate_branch_name(policy: dict[str, Any], ref: str) -> None:
    allowed_refs = {f"refs/heads/{name}" for name in policy["branches"].get("long_lived", [])}
    if ref in allowed_refs:
        return
    for family in policy["branches"].get("families", []):
        if matches_ref_pattern(f"refs/heads/{family}", ref):
            return
    raise HookReject(f"branch name is not allowed by policy: {ref}")


def validate_branch_creation(repo: Path, policy: dict[str, Any], update: RefUpdate) -> None:
    if update.ref in set(policy.get("protected_refs", [])):
        return

    edge = first_matching(policy.get("branch_from", []), "target_ref_regex", update.ref)
    if not edge:
        raise HookReject(f"branch creation is not allowed by policy: {update.ref}")

    source_ref = edge["source_ref"]
    if not ref_exists(repo, source_ref):
        raise HookReject(f"required branch source does not exist: {source_ref}")
    if rev_parse(repo, source_ref) != update.new:
        raise HookReject(f"{update.ref} must branch from {source_ref}")


def validate_protected_target_update(
    repo: Path,
    policy: dict[str, Any],
    proposed: dict[str, str],
    update: RefUpdate,
) -> None:
    if update.old == ZERO:
        return
    if update.new == ZERO:
        raise HookReject(f"deleting protected ref is not allowed: {update.ref}")
    if not is_ancestor(repo, update.old, update.new):
        raise HookReject(f"non-fast-forward protected ref update is not allowed: {update.ref}")

    candidates = source_candidates_for_target(repo, policy, update)
    if not candidates:
        raise HookReject(f"protected ref update has no allowed source branch: {update.ref}")
    if len(candidates) > 1:
        joined = ", ".join(candidate.ref for candidate in candidates)
        raise HookReject(f"protected ref update matches multiple source branches: {joined}")

    candidate = candidates[0]
    required = required_target_refs(policy, candidate.rule["source"])
    if len(required) <= 1:
        return

    completed_before = [target_ref for target_ref in required if ref_contains(repo, target_ref, candidate.sha)]
    if update.ref in completed_before:
        return

    next_index = len(completed_before)
    next_required = required[next_index] if next_index < len(required) else None
    if update.ref != next_required:
        raise HookReject(f"{update.ref} cannot receive {candidate.ref}@{candidate.sha[:12]} before {next_required}")


def source_candidates_for_target(repo: Path, policy: dict[str, Any], update: RefUpdate) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    for rule in policy.get("merge_rules", []):
        if rule.get("target_ref") != update.ref:
            continue
        for ref in refs_matching(repo, rule["source_ref_regex"]):
            sha = rev_parse(repo, ref)
            if is_ancestor(repo, sha, update.new) and not is_ancestor(repo, sha, update.old):
                candidates.append(SourceCandidate(ref=ref, sha=sha, rule=rule))
    return candidates


def validate_tag(repo: Path, policy: dict[str, Any], proposed: dict[str, str], update: RefUpdate) -> None:
    if update.old != ZERO:
        raise HookReject(f"moving existing tags is not allowed: {update.ref}")
    rules = [rule for rule in policy.get("tag_rules", []) if re.match(rule["tag_ref_regex"], update.ref)]
    if not rules:
        raise HookReject(f"tag name is not allowed by policy: {update.ref}")
    tag_version = parse_version_ref(update.ref)
    state = load_state(Path(os.environ.get("GFG_STATE_JSON", repo / ".git" / "gfg-state.json")))

    for rule in rules:
        required = required_target_refs(policy, rule["source"])
        source_regex = source_ref_regex(policy, rule["source"])
        source_refs = refs_matching(repo, source_regex)
        targets_contain_tag_target = required and all(
            ref_contains(repo, proposed.get(target_ref, target_ref), update.new) for target_ref in required
        )
        if not targets_contain_tag_target:
            continue

        for source_ref in source_refs:
            if rev_parse(repo, source_ref) != update.new:
                continue
            if tag_rule_allows_version(repo, state, rule, source_ref, tag_version):
                return

    raise HookReject(f"tag target is not attached to the required source family and version line: {update.ref}")


def tag_rule_allows_version(
    repo: Path,
    state: dict[str, Any],
    rule: dict[str, Any],
    source_ref: str,
    tag_version: tuple[int, int, int],
) -> bool:
    tokens = rule.get("tag_tokens", [])
    if not tag_tokens_match(tokens, tag_version):
        return False

    if "=" in tokens:
        base = state.get("branch_bases", {}).get(source_ref)
        if not base or not base.get("base_release_tag"):
            raise HookReject(f"{source_ref} has no recorded base release tag")
        base_version = parse_version_name(base["base_release_tag"])
        if tag_version[:2] != base_version[:2]:
            raise HookReject(
                f"{source_ref} tag must stay on base release line v{base_version[0]}.{base_version[1]}"
            )
        return tag_version > max_existing_version(repo, major=base_version[0], minor=base_version[1])

    latest = max_existing_version(repo)
    if latest is None:
        return True
    return tag_version > latest


def tag_tokens_match(tokens: list[str], version: tuple[int, int, int]) -> bool:
    if len(tokens) != 3:
        return False
    for token, component in zip(tokens, version):
        if token in {"#", "="}:
            continue
        if int(token) != component:
            return False
    return True


def max_existing_version(repo: Path, major: int | None = None, minor: int | None = None) -> tuple[int, int, int] | None:
    versions: list[tuple[int, int, int]] = []
    for ref in git(repo, "for-each-ref", "--format=%(refname)", "refs/tags").stdout.splitlines():
        try:
            version = parse_version_ref(ref)
        except HookReject:
            continue
        if major is not None and version[0] != major:
            continue
        if minor is not None and version[1] != minor:
            continue
        versions.append(version)
    if not versions:
        return None
    return max(versions)


def latest_reachable_release_tag(repo: Path, commit: str) -> str | None:
    tags: list[tuple[tuple[int, int, int], str]] = []
    for ref in git(repo, "for-each-ref", "--format=%(refname)", "refs/tags").stdout.splitlines():
        try:
            version = parse_version_ref(ref)
        except HookReject:
            continue
        if version[2] != 0:
            continue
        if is_ancestor(repo, ref, commit):
            tags.append((version, ref.removeprefix("refs/tags/")))
    if not tags:
        return None
    return max(tags)[1]


def parse_version_ref(ref: str) -> tuple[int, int, int]:
    if ref.startswith("refs/tags/"):
        return parse_version_name(ref.removeprefix("refs/tags/"))
    return parse_version_name(ref)


def parse_version_name(name: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v([0-9]+)\.([0-9]+)\.([0-9]+)", name)
    if not match:
        raise HookReject(f"invalid version tag: {name}")
    return tuple(int(part) for part in match.groups())


def enforce_pending_lock(repo: Path, pending: dict[str, Any], update: RefUpdate) -> None:
    if not pending or update.new == ZERO or not update.ref.startswith("refs/heads/"):
        return

    for source_ref, item in pending.items():
        source_sha = item["source_sha"]
        remaining = set(item["remaining_target_refs"])

        if update.ref == source_ref and update.new != source_sha:
            raise HookReject(f"{source_ref} cannot move before required targets receive {source_sha[:12]}")

        if update.ref in remaining:
            if not is_ancestor(repo, source_sha, update.new):
                raise HookReject(f"{update.ref} must receive pending {source_ref}@{source_sha[:12]}")
            return

        if update.ref not in set(item["completed_target_refs"]):
            raise HookReject(f"complete pending multi-target merge for {source_ref}@{source_sha[:12]} first")


def update_committed_state(repo: Path, policy: dict[str, Any], state_path: Path, updates: list[RefUpdate]) -> None:
    state = load_state(state_path)
    pending = state.setdefault("pending", {})
    branch_bases = state.setdefault("branch_bases", {})

    for update in updates:
        if update.ref.startswith("refs/heads/") and update.old == ZERO and update.new != ZERO:
            edge = first_matching(policy.get("branch_from", []), "target_ref_regex", update.ref)
            if edge:
                branch_bases[update.ref] = {
                    "source_ref": edge["source_ref"],
                    "base_sha": update.new,
                    "base_release_tag": latest_reachable_release_tag(repo, update.new),
                }

        if update.ref not in set(policy.get("protected_refs", [])) or update.new == ZERO:
            continue

        candidates = source_candidates_for_target(repo, policy, update)
        if len(candidates) != 1:
            continue

        candidate = candidates[0]
        required = required_target_refs(policy, candidate.rule["source"])
        if len(required) <= 1:
            continue

        completed = [target_ref for target_ref in required if ref_contains(repo, target_ref, candidate.sha)]
        if set(completed) == set(required):
            pending.pop(candidate.ref, None)
        else:
            pending[candidate.ref] = {
                "source_sha": candidate.sha,
                "required_target_refs": required,
                "completed_target_refs": completed,
                "remaining_target_refs": [ref for ref in required if ref not in completed],
            }

    save_state(state_path, state)


def required_target_refs(policy: dict[str, Any], source_pattern: str) -> list[str]:
    for item in policy.get("required_targets", []):
        if item.get("source") == source_pattern:
            return list(item.get("target_refs", []))
    return []


def source_ref_regex(policy: dict[str, Any], source_pattern: str) -> str:
    for item in policy.get("required_targets", []):
        if item.get("source") == source_pattern:
            return item["source_ref_regex"]
    raise HookReject(f"source pattern has no generated source ref regex: {source_pattern}")


def read_updates(stdin: Any) -> list[RefUpdate]:
    updates: list[RefUpdate] = []
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(" ", 2)
        if len(parts) != 3:
            raise HookReject(f"invalid reference-transaction input line: {line}")
        updates.append(RefUpdate(old=parts[0], new=parts[1], ref=parts[2]))
    return updates


def append_log(path: Path, phase: str, updates: list[RefUpdate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for update in updates:
            stream.write(f"{phase} {update.old} {update.new} {update.ref}\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"pending": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def refs_matching(repo: Path, pattern: str) -> list[str]:
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/heads").stdout.splitlines()
    return [ref for ref in refs if re.match(pattern, ref)]


def first_matching(items: list[dict[str, Any]], key: str, ref: str) -> dict[str, Any] | None:
    for item in items:
        if re.match(item[key], ref):
            return item
    return None


def matches_ref_pattern(pattern: str, ref: str) -> bool:
    regex = "^" + re.escape(pattern).replace("\\*", ".+") + "$"
    return re.match(regex, ref) is not None


def ref_exists(repo: Path, ref: str) -> bool:
    return git(repo, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def rev_parse(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", ref).stdout.strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    if ancestor == ZERO or descendant == ZERO:
        return False
    return git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def ref_contains(repo: Path, ref_or_sha: str, sha: str) -> bool:
    if ref_or_sha.startswith("refs/") and not ref_exists(repo, ref_or_sha):
        return False
    return is_ancestor(repo, sha, ref_or_sha)


def is_policy_ref(ref: str) -> bool:
    return ref.startswith("refs/heads/") or ref.startswith("refs/tags/")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise HookReject(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HookReject(f"missing required environment variable: {name}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
