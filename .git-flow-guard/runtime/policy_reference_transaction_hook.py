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
AGENT_REJECT_HINT = (
    "if you are an agent, read the contribution document and use the configured workflow; "
    "do not try to bypass this hook."
)


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
    def __init__(self, code: str, **context: Any) -> None:
        self.code = code
        self.context = context
        super().__init__(format_reject(code, context))


def format_reject(code: str, context: dict[str, Any]) -> str:
    if not context:
        return code
    fields = [f"{key}={format_context_value(value)}" for key, value in context.items()]
    return " ".join([code, *fields])


def format_context_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ",".join(format_context_value(item) for item in value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:@+=,-]+", text):
        return text
    return json.dumps(text, ensure_ascii=True)


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
            raise HookReject("HOOK_UNSUPPORTED_PHASE", phase=phase)
    except HookReject as exc:
        print(f"git-flow-guard: {exc}", file=sys.stderr)
        source_path = policy.get("source", {}).get("path")
        if source_path:
            print(f"git-flow-guard: see policy: {source_path}", file=sys.stderr)
        print(f"git-flow-guard: agent guidance: {AGENT_REJECT_HINT}", file=sys.stderr)
        return 1

    return 0


def validate_prepared(repo: Path, policy: dict[str, Any], state_path: Path, updates: list[RefUpdate]) -> None:
    state = load_state(state_path)
    pending = state.get("pending", {})
    pending_tags = state.get("pending_tags", {})
    proposed = {update.ref: update.new for update in updates if update.new != ZERO}

    for update in updates:
        if not is_policy_ref(update.ref):
            continue

        if update.ref.startswith("refs/heads/"):
            validate_branch_name(policy, update.ref)

        enforce_pending_lock(repo, pending, update)
        enforce_pending_tag_lock(repo, policy, pending_tags, update)

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
    raise HookReject("BRANCH_NAME_NOT_ALLOWED", ref=ref)


def validate_branch_creation(repo: Path, policy: dict[str, Any], update: RefUpdate) -> None:
    if update.ref in set(policy.get("protected_refs", [])):
        return

    edge = first_matching(policy.get("branch_from", []), "target_ref_regex", update.ref)
    if not edge:
        raise HookReject("BRANCH_CREATION_NOT_ALLOWED", ref=update.ref)

    source_ref = edge["source_ref"]
    if not ref_exists(repo, source_ref):
        raise HookReject("BRANCH_SOURCE_MISSING", ref=update.ref, source_ref=source_ref)
    if rev_parse(repo, source_ref) != update.new:
        raise HookReject("BRANCH_SOURCE_MISMATCH", ref=update.ref, source_ref=source_ref, new=short_sha(update.new))


def validate_protected_target_update(
    repo: Path,
    policy: dict[str, Any],
    proposed: dict[str, str],
    update: RefUpdate,
) -> None:
    if update.old == ZERO:
        return
    if update.new == ZERO:
        raise HookReject("PROTECTED_REF_DELETE", ref=update.ref)
    if not is_ancestor(repo, update.old, update.new):
        raise HookReject("PROTECTED_REF_NON_FAST_FORWARD", ref=update.ref, old=short_sha(update.old), new=short_sha(update.new))

    candidates = source_candidates_for_target(repo, policy, update)
    if not candidates:
        raise HookReject("PROTECTED_REF_NO_ALLOWED_SOURCE", ref=update.ref, old=short_sha(update.old), new=short_sha(update.new))
    if len(candidates) > 1:
        raise HookReject(
            "PROTECTED_REF_MULTIPLE_SOURCES",
            ref=update.ref,
            sources=[candidate.ref for candidate in candidates],
        )

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
        raise HookReject(
            "MULTI_TARGET_ORDER",
            ref=update.ref,
            source_ref=candidate.ref,
            source_sha=short_sha(candidate.sha),
            expected_ref=next_required,
        )


def source_candidates_for_target(repo: Path, policy: dict[str, Any], update: RefUpdate) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    for rule in policy.get("merge_rules", []):
        if rule.get("target_ref") != update.ref:
            continue
        for ref in refs_matching(repo, rule["source_ref_regex"]):
            sha = rev_parse(repo, ref)
            if is_ancestor(repo, sha, update.new) and not is_ancestor(repo, sha, update.old):
                candidates.append(SourceCandidate(ref=ref, sha=sha, rule=rule))
    return maximal_source_candidates(repo, candidates)


def maximal_source_candidates(repo: Path, candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    maximal = []
    for candidate in candidates:
        if any(candidate.sha != other.sha and is_ancestor(repo, candidate.sha, other.sha) for other in candidates):
            continue
        maximal.append(candidate)
    return maximal


def validate_tag(repo: Path, policy: dict[str, Any], proposed: dict[str, str], update: RefUpdate) -> None:
    if update.old != ZERO:
        raise HookReject("TAG_MOVE_NOT_ALLOWED", tag=update.ref, old=short_sha(update.old), new=short_sha(update.new))
    source_head_matches = tag_source_head_matches(repo, policy, update.ref, update.new)
    if source_head_matches and not [item for item in source_head_matches if item["tag_matches"]]:
        raise HookReject(
            "TAG_SOURCE_TAG_PATTERN_MISMATCH",
            tag=update.ref,
            target=short_sha(update.new),
            source_refs=[item["source_ref"] for item in source_head_matches],
            allowed_patterns=[item["tag_pattern"] for item in source_head_matches],
        )

    rules = [rule for rule in policy.get("tag_rules", []) if re.match(rule["tag_ref_regex"], update.ref)]
    if not rules:
        raise HookReject("TAG_NAME_NOT_ALLOWED", tag=update.ref)
    tag_version = parse_version_ref(update.ref)
    state = load_state(Path(os.environ.get("GFG_STATE_JSON", repo / ".git" / "gfg-state.json")))
    failures: list[HookReject] = []

    for rule in rules:
        required = required_target_refs(policy, rule["source"])
        source_regex = source_ref_regex(policy, rule["source"])
        source_refs = refs_matching(repo, source_regex)
        if not source_refs:
            failures.append(HookReject("TAG_SOURCE_BRANCH_MISSING", tag=update.ref, source=rule["source"]))
            continue

        missing_targets = [
            target_ref
            for target_ref in required
            if not ref_contains(repo, proposed.get(target_ref, target_ref), update.new)
        ]
        if missing_targets:
            failures.append(
                HookReject(
                    "TAG_REQUIRED_TARGETS_MISSING",
                    tag=update.ref,
                    target=short_sha(update.new),
                    source=rule["source"],
                    missing=missing_targets,
                )
            )
            continue

        matched_source_refs = []
        for source_ref in source_refs:
            if rev_parse(repo, source_ref) != update.new:
                continue
            matched_source_refs.append(source_ref)
            if tag_rule_allows_version(repo, state, rule, source_ref, update.ref, tag_version):
                return

        if not matched_source_refs:
            failures.append(
                HookReject(
                    "TAG_TARGET_NOT_SOURCE_HEAD",
                    tag=update.ref,
                    target=short_sha(update.new),
                    source=rule["source"],
                    source_refs=source_refs,
                )
            )

    raise preferred_tag_failure(failures, update.ref)


def tag_source_head_matches(repo: Path, policy: dict[str, Any], tag_ref: str, target_sha: str) -> list[dict[str, Any]]:
    matches = []
    for rule in policy.get("tag_rules", []):
        source_regex = source_ref_regex(policy, rule["source"])
        for source_ref in refs_matching(repo, source_regex):
            if rev_parse(repo, source_ref) != target_sha:
                continue
            matches.append(
                {
                    "source": rule["source"],
                    "source_ref": source_ref,
                    "tag_pattern": rule.get("tag_pattern"),
                    "tag_matches": re.match(rule["tag_ref_regex"], tag_ref) is not None,
                }
            )
    return matches


def tag_rule_allows_version(
    repo: Path,
    state: dict[str, Any],
    rule: dict[str, Any],
    source_ref: str,
    tag_ref: str,
    tag_version: tuple[int, ...],
) -> bool:
    tokens = rule.get("tag_tokens", [])
    if not tag_tokens_match(tokens, tag_version):
        raise HookReject("TAG_PATTERN_COMPONENT_MISMATCH", tag=tag_ref, pattern=rule.get("tag_pattern"))

    if "=" in tokens:
        base = state.get("branch_bases", {}).get(source_ref)
        if not base or not base.get("base_release_tag"):
            raise HookReject("TAG_BASE_RELEASE_MISSING", tag=tag_ref, source_ref=source_ref)
        base_version = parse_version_name(base["base_release_tag"])
        if tag_version[:2] != base_version[:2]:
            raise HookReject(
                "TAG_VERSION_LINE_MISMATCH",
                tag=tag_ref,
                source_ref=source_ref,
                expected_major=base_version[0],
                expected_minor=base_version[1],
                actual_major=tag_version[0],
                actual_minor=tag_version[1],
            )
        latest = max_existing_version(repo, major=base_version[0], minor=base_version[1])
        if latest is None or tag_version > latest:
            return True
        raise HookReject("TAG_VERSION_NOT_INCREMENTAL", tag=tag_ref, version=format_version(tag_version), latest=format_version(latest))

    latest = max_existing_version(repo)
    if latest is None:
        return True
    if tag_version > latest:
        return True
    raise HookReject("TAG_VERSION_NOT_INCREMENTAL", tag=tag_ref, version=format_version(tag_version), latest=format_version(latest))


def preferred_tag_failure(failures: list[HookReject], tag_ref: str) -> HookReject:
    if not failures:
        return HookReject("TAG_RULE_NOT_SATISFIED", tag=tag_ref)
    priority = {
        "TAG_VERSION_LINE_MISMATCH": 0,
        "TAG_VERSION_NOT_INCREMENTAL": 1,
        "TAG_PATTERN_COMPONENT_MISMATCH": 2,
        "TAG_BASE_RELEASE_MISSING": 3,
        "TAG_TARGET_NOT_SOURCE_HEAD": 4,
        "TAG_REQUIRED_TARGETS_MISSING": 5,
        "TAG_SOURCE_BRANCH_MISSING": 6,
    }
    return min(failures, key=lambda failure: priority.get(failure.code, 100))


def tag_tokens_match(tokens: list[str], version: tuple[int, ...]) -> bool:
    if len(tokens) != len(version):
        return False
    for token, component in zip(tokens, version):
        if token in {"#", "="}:
            continue
        if int(token) != component:
            return False
    return True


def max_existing_version(repo: Path, major: int | None = None, minor: int | None = None) -> tuple[int, ...] | None:
    versions: list[tuple[int, ...]] = []
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
    tags: list[tuple[tuple[int, ...], str]] = []
    for ref in git(repo, "for-each-ref", "--format=%(refname)", "refs/tags").stdout.splitlines():
        try:
            version = parse_version_ref(ref)
        except HookReject:
            continue
        if len(version) != 3 or version[2] != 0:
            continue
        if is_ancestor(repo, ref, commit):
            tags.append((version, ref.removeprefix("refs/tags/")))
    if not tags:
        return None
    return max(tags)[1]


def parse_version_ref(ref: str) -> tuple[int, ...]:
    if ref.startswith("refs/tags/"):
        return parse_version_name(ref.removeprefix("refs/tags/"))
    return parse_version_name(ref)


def parse_version_name(name: str) -> tuple[int, ...]:
    match = re.fullmatch(r"[vV]([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", name)
    if not match:
        raise HookReject("TAG_VERSION_INVALID", tag=name)
    return tuple(int(part) for part in match.groups() if part is not None)


def enforce_pending_lock(repo: Path, pending: dict[str, Any], update: RefUpdate) -> None:
    if not pending or update.new == ZERO or not update.ref.startswith("refs/heads/"):
        return

    for source_ref, item in pending.items():
        source_sha = item["source_sha"]
        remaining = set(item["remaining_target_refs"])

        if update.ref == source_ref and update.new != source_sha:
            raise HookReject(
                "PENDING_SOURCE_MOVED",
                source_ref=source_ref,
                expected_sha=short_sha(source_sha),
                new=short_sha(update.new),
            )

        if update.ref in remaining:
            if not is_ancestor(repo, source_sha, update.new):
                raise HookReject(
                    "PENDING_TARGET_MISSING_SOURCE",
                    ref=update.ref,
                    source_ref=source_ref,
                    source_sha=short_sha(source_sha),
                )
            return

        if update.ref not in set(item["completed_target_refs"]):
            raise HookReject(
                "PENDING_MULTI_TARGET_INCOMPLETE",
                ref=update.ref,
                source_ref=source_ref,
                source_sha=short_sha(source_sha),
                remaining=sorted(remaining),
            )


def enforce_pending_tag_lock(
    repo: Path,
    policy: dict[str, Any],
    pending_tags: dict[str, Any],
    update: RefUpdate,
) -> None:
    if not pending_tags or not update.ref.startswith("refs/heads/"):
        return

    for _, item in pending_tag_items(pending_tags):
        source_sha = item["source_sha"]
        if update.ref == item["source_ref"] and update.new != source_sha:
            raise HookReject(
                "PENDING_TAG_SOURCE_MOVED",
                source_ref=item["source_ref"],
                expected_sha=short_sha(source_sha),
                new=short_sha(update.new),
                target_ref=item["target_ref"],
                tag_pattern=item["tag_pattern"],
            )

        if update.ref != item["target_ref"] or update.new == ZERO:
            continue

        for candidate in source_candidates_for_target(repo, policy, update):
            if pending_tag_matches_rule(item, candidate.rule):
                raise HookReject(
                    "PENDING_TAG_REQUIRED",
                    source_ref=item["source_ref"],
                    source_sha=short_sha(source_sha),
                    target_ref=item["target_ref"],
                    tag_pattern=item["tag_pattern"],
                )


def update_committed_state(repo: Path, policy: dict[str, Any], state_path: Path, updates: list[RefUpdate]) -> None:
    state = load_state(state_path)
    pending = state.setdefault("pending", {})
    pending_tags = state.setdefault("pending_tags", {})
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
        update_pending_tags(repo, pending_tags, candidate)

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

    clear_satisfied_pending_tags(pending_tags, updates)
    save_state(state_path, state)


def update_pending_tags(repo: Path, pending_tags: dict[str, Any], candidate: SourceCandidate) -> None:
    rule = candidate.rule
    tag_pattern = rule.get("tag_pattern")
    tag_ref_regex = rule.get("tag_ref_regex")
    if not tag_pattern or not tag_ref_regex:
        return

    key = pending_tag_key(candidate.ref, rule["target_ref"], tag_pattern)
    if matching_tag_exists(repo, tag_ref_regex, candidate.sha):
        pending_tags.pop(key, None)
        return

    pending_tags[key] = {
        "source": rule["source"],
        "target": rule["target"],
        "source_ref": candidate.ref,
        "source_sha": candidate.sha,
        "target_ref": rule["target_ref"],
        "merge_rule_id": rule["id"],
        "tag_pattern": tag_pattern,
        "tag_ref_regex": tag_ref_regex,
    }


def clear_satisfied_pending_tags(pending_tags: dict[str, Any], updates: list[RefUpdate]) -> None:
    for update in updates:
        if not update.ref.startswith("refs/tags/") or update.new == ZERO:
            continue
        for key, item in pending_tag_items(pending_tags):
            if update.new == item["source_sha"] and re.match(item["tag_ref_regex"], update.ref):
                pending_tags.pop(key, None)


def pending_tag_key(source_ref: str, target_ref: str, tag_pattern: str) -> str:
    return "|".join([source_ref, target_ref, tag_pattern])


def pending_tag_items(pending_tags: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(key, item) for key, item in pending_tags.items() if isinstance(item, dict)]


def pending_tag_matches_rule(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    return (
        item.get("source") == rule.get("source")
        and item.get("target_ref") == rule.get("target_ref")
        and item.get("tag_pattern") == rule.get("tag_pattern")
    )


def matching_tag_exists(repo: Path, tag_ref_regex: str, target_sha: str) -> bool:
    for ref in git(repo, "for-each-ref", "--format=%(refname)", "refs/tags").stdout.splitlines():
        if re.match(tag_ref_regex, ref) and rev_parse(repo, ref) == target_sha:
            return True
    return False


def required_target_refs(policy: dict[str, Any], source_pattern: str) -> list[str]:
    for item in policy.get("required_targets", []):
        if item.get("source") == source_pattern:
            return list(item.get("target_refs", []))
    return []


def source_ref_regex(policy: dict[str, Any], source_pattern: str) -> str:
    for item in policy.get("required_targets", []):
        if item.get("source") == source_pattern:
            return item["source_ref_regex"]
    raise HookReject("POLICY_SOURCE_REGEX_MISSING", source=source_pattern)


def read_updates(stdin: Any) -> list[RefUpdate]:
    updates: list[RefUpdate] = []
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(" ", 2)
        if len(parts) != 3:
            raise HookReject("HOOK_INPUT_INVALID", line=line)
        updates.append(RefUpdate(old=parts[0], new=parts[1], ref=parts[2]))
    return updates


def append_log(path: Path, phase: str, updates: list[RefUpdate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for update in updates:
            stream.write(f"{phase} {update.old} {update.new} {update.ref}\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"pending": {}, "pending_tags": {}}
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
        raise HookReject("GIT_COMMAND_FAILED", command="git " + " ".join(args), stderr=result.stderr.strip())
    return result


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HookReject("ENV_MISSING", name=name)
    return value


def short_sha(value: str | None) -> str | None:
    if value is None:
        return None
    if value == ZERO:
        return ZERO
    return value[:12]


def format_version(version: tuple[int, ...] | None) -> str | None:
    if version is None:
        return None
    return "v" + ".".join(str(part) for part in version)


if __name__ == "__main__":
    raise SystemExit(main())
