from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Any


class PolicyParseError(ValueError):
    """Raised when a contribution.md gitGraph cannot be converted to policy."""


@dataclass(frozen=True)
class BranchFrom:
    source: str
    target: str


@dataclass(frozen=True)
class MergeRule:
    source: str
    target: str
    label: str
    tag: str | None
    tag_required: bool | None = None


@dataclass(frozen=True)
class ParsedGraph:
    branch_from: list[BranchFrom]
    merge_rules: list[MergeRule]
    branches: list[str]
    direct_commit_branches: list[str]


_MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)


def load_policy_from_markdown(path: Path) -> dict[str, Any]:
    markdown = path.read_text(encoding="utf-8")
    block = extract_gitgraph_block(markdown)
    graph = parse_gitgraph(block)
    return graph_to_policy(graph, source_file=path.name)


def extract_gitgraph_block(markdown: str) -> str:
    for block in _MERMAID_BLOCK_RE.findall(markdown):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and lines[0].startswith("gitGraph"):
            return block
    raise PolicyParseError("No Mermaid gitGraph block found.")


def parse_gitgraph(block: str) -> ParsedGraph:
    current_branch = "main"
    branches = ["main"]
    checked_out_branches: set[str] = set()
    branch_from: list[BranchFrom] = []
    merge_rules: list[MergeRule] = []
    direct_commit_branches: list[str] = []

    for line_number, raw_line in enumerate(block.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue
        if line.startswith("gitGraph"):
            continue

        try:
            tokens = shlex.split(line, posix=True)
        except ValueError as exc:
            raise PolicyParseError(f"Line {line_number}: cannot parse statement: {raw_line}") from exc

        if not tokens:
            continue

        command = tokens[0]
        if command == "commit":
            if current_branch != "main" and current_branch in checked_out_branches:
                _append_unique(direct_commit_branches, current_branch)
            continue
        if command == "branch":
            _require_args(tokens, 2, line_number)
            target = tokens[1]
            branch_from.append(BranchFrom(source=current_branch, target=target))
            _append_unique(branches, target)
            continue
        if command == "checkout":
            _require_args(tokens, 2, line_number)
            current_branch = tokens[1]
            _append_unique(branches, current_branch)
            checked_out_branches.add(current_branch)
            continue
        if command == "merge":
            _require_args(tokens, 2, line_number)
            source = tokens[1]
            attrs = _parse_attrs(tokens[2:], line_number)
            label = attrs.get("id")
            if not label:
                raise PolicyParseError(f'Line {line_number}: merge "{source}" is missing id:"...".')
            merge_rules.append(
                MergeRule(
                    source=source,
                    target=current_branch,
                    label=merge_rule_id(source, current_branch),
                    tag=attrs.get("tag"),
                )
            )
            _append_unique(branches, source)
            _append_unique(branches, current_branch)
            continue

        raise PolicyParseError(f'Line {line_number}: unsupported gitGraph statement "{command}".')

    return ParsedGraph(
        branch_from=branch_from,
        merge_rules=normalize_merge_rules(merge_rules),
        branches=branches,
        direct_commit_branches=direct_commit_branches,
    )


def graph_to_policy(graph: ParsedGraph, source_file: str) -> dict[str, Any]:
    long_lived = [branch for branch in graph.branches if not _is_pattern(branch)]
    families = [branch for branch in graph.branches if _is_pattern(branch)]
    protected_targets = _unique(
        ref_pattern(rule.target) for rule in graph.merge_rules if not _is_pattern(rule.target)
    )

    merge_rules = [
        {
            "source": rule.source,
            "target": rule.target,
            "id": rule.label,
            "source_ref": ref_pattern(rule.source),
            "source_ref_regex": ref_regex(rule.source),
            "target_ref": ref_pattern(rule.target),
            "tag_pattern": rule.tag,
            "tag_required": rule.tag_required if rule.tag else None,
            "tag_tokens": tag_tokens(rule.tag) if rule.tag else None,
            "tag_ref": ref_pattern(rule.tag, ref_kind="tags") if rule.tag else None,
            "tag_ref_regex": tag_regex(rule.tag) if rule.tag else None,
        }
        for rule in graph.merge_rules
    ]

    return _drop_none(
        {
            "version": 1,
            "source": {
                "file": source_file,
                "format": "mermaid.gitGraph",
            },
            "branches": {
                "long_lived": long_lived,
                "families": families,
            },
            "protected_refs": protected_targets,
            "direct_commit_refs": [
                {
                    "name": branch,
                    "ref": ref_pattern(branch),
                    "ref_regex": ref_regex(branch),
                }
                for branch in graph.direct_commit_branches
            ],
            "branch_from": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "source_ref": ref_pattern(edge.source),
                    "target_ref": ref_pattern(edge.target),
                    "target_ref_regex": ref_regex(edge.target),
                }
                for edge in graph.branch_from
            ],
            "merge_rules": merge_rules,
            "required_targets": required_targets(graph.merge_rules),
            "tag_rules": [
                {
                    "source": rule.source,
                    "target": rule.target,
                    "tag_pattern": rule.tag,
                    "tag_required": rule.tag_required,
                    "tag_tokens": tag_tokens(rule.tag),
                    "tag_ref": ref_pattern(rule.tag, ref_kind="tags"),
                    "tag_ref_regex": tag_regex(rule.tag),
                }
                for rule in graph.merge_rules
                if rule.tag
            ],
        }
    )


def normalize_merge_rules(rules: list[MergeRule]) -> list[MergeRule]:
    grouped: dict[tuple[str, str], list[MergeRule]] = {}
    order: list[tuple[str, str]] = []

    for rule in rules:
        key = (rule.source, rule.target)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(rule)

    normalized: list[MergeRule] = []
    for key in order:
        group = grouped[key]
        untagged = [rule for rule in group if rule.tag is None]
        tagged = [rule for rule in group if rule.tag is not None]
        label = merge_rule_id(*key)

        if len(untagged) > 1:
            raise PolicyParseError(f'Duplicate untagged merge rule "{label}".')
        if len(tagged) > 1:
            patterns = ", ".join(str(rule.tag) for rule in tagged)
            raise PolicyParseError(f'Duplicate tagged merge rule "{label}" with tag patterns: {patterns}.')

        if untagged and tagged:
            tagged_rule = tagged[0]
            normalized.append(
                MergeRule(
                    source=tagged_rule.source,
                    target=tagged_rule.target,
                    label=tagged_rule.label,
                    tag=tagged_rule.tag,
                    tag_required=False,
                )
            )
            continue

        if tagged:
            tagged_rule = tagged[0]
            normalized.append(
                MergeRule(
                    source=tagged_rule.source,
                    target=tagged_rule.target,
                    label=tagged_rule.label,
                    tag=tagged_rule.tag,
                    tag_required=True,
                )
            )
            continue

        normalized.append(untagged[0])

    return normalized


def merge_rule_id(source: str, target: str) -> str:
    return f"{source} to {target}"


def required_targets(rules: list[MergeRule]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for rule in rules:
        grouped.setdefault(rule.source, [])
        _append_unique(grouped[rule.source], rule.target)
    return [
        {
            "source": source,
            "source_ref": ref_pattern(source),
            "source_ref_regex": ref_regex(source),
            "targets": targets,
            "target_refs": [ref_pattern(target) for target in targets],
        }
        for source, targets in grouped.items()
    ]


def ref_pattern(name: str, ref_kind: str = "heads") -> str:
    return f"refs/{ref_kind}/{name}"


def ref_regex(name: str, ref_kind: str = "heads") -> str:
    return "^" + _pattern_to_regex(ref_pattern(name, ref_kind=ref_kind), star_replacement=".+") + "$"


def tag_regex(pattern: str) -> str:
    return (
        "^refs/tags/"
        + _pattern_to_regex(pattern, star_replacement=".+", hash_replacement="[0-9]+", equals_replacement="[0-9]+")
        + "$"
    )


def tag_tokens(pattern: str) -> list[str]:
    match = re.fullmatch(r"[vV]([#=0-9]+)\.([#=0-9]+)(?:\.([#=0-9]+))?", pattern)
    if not match:
        raise PolicyParseError(f'Unsupported tag pattern "{pattern}". Expected v#.#.0, v=.=.#, or V#.#.')
    return [part for part in match.groups() if part is not None]


def dump_yaml(data: Any) -> str:
    return _dump_yaml_value(data, indent=0).rstrip() + "\n"


def _dump_yaml_value(value: Any, indent: int) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if _is_scalar(item):
                lines.append(f"{prefix}{key}: {_format_scalar(item)}")
            else:
                lines.append(f"{prefix}{key}:")
                lines.append(_dump_yaml_value(item, indent + 2))
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{prefix}[]"
        lines = []
        for item in value:
            if _is_scalar(item):
                lines.append(f"{prefix}- {_format_scalar(item)}")
            else:
                lines.append(f"{prefix}-")
                lines.append(_dump_yaml_value(item, indent + 2))
        return "\n".join(lines)
    return f"{prefix}{_format_scalar(value)}"


def _parse_attrs(tokens: list[str], line_number: int) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for token in tokens:
        if ":" not in token:
            raise PolicyParseError(f'Line {line_number}: invalid attribute "{token}".')
        key, value = token.split(":", 1)
        attrs[key] = value
    return attrs


def _pattern_to_regex(
    pattern: str,
    star_replacement: str,
    hash_replacement: str | None = None,
    equals_replacement: str | None = None,
) -> str:
    parts: list[str] = []
    for char in pattern:
        if char == "*":
            parts.append(star_replacement)
        elif char == "#" and hash_replacement is not None:
            parts.append(hash_replacement)
        elif char == "=" and equals_replacement is not None:
            parts.append(equals_replacement)
        else:
            parts.append(re.escape(char))
    return "".join(parts)


def _is_pattern(name: str) -> bool:
    return "*" in name


def _require_args(tokens: list[str], count: int, line_number: int) -> None:
    if len(tokens) < count:
        raise PolicyParseError(f'Line {line_number}: "{tokens[0]}" expects at least {count - 1} argument.')


def _append_unique(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def _unique(items: Any) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'
