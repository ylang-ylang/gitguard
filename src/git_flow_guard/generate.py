from __future__ import annotations

import argparse
from pathlib import Path

from git_flow_guard.mermaid import PolicyParseError, dump_yaml, load_policy_from_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate policy.yaml from config contribution.md files.")
    parser.add_argument(
        "config",
        nargs="?",
        help="Config directory containing contribution.md. Omit with --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate policy.yaml for each configs/* directory that contains contribution.md.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if generated policy.yaml differs from the file on disk.",
    )
    args = parser.parse_args()
    return run_generate_command(config=args.config, all_configs=args.all, check=args.check, parser=parser)


def run_generate_command(
    config: str | None,
    all_configs: bool,
    check: bool,
    parser: argparse.ArgumentParser,
) -> int:
    try:
        config_dirs = _select_config_dirs(config, all_configs)
        for config_dir in config_dirs:
            generate_config(config_dir, check=check)
    except PolicyParseError as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


def generate_config(config_dir: Path, check: bool = False) -> None:
    source_path = config_dir / "contribution.md"
    output_path = config_dir / "policy.yaml"
    if not source_path.exists():
        raise PolicyParseError(f"{source_path} does not exist.")

    rendered = render_policy_yaml(source_path)

    if check:
        if not output_path.exists():
            raise PolicyParseError(f"{output_path} does not exist.")
        current = output_path.read_text(encoding="utf-8")
        if current != rendered:
            raise PolicyParseError(f"{output_path} is out of date.")
        print(f"ok: {output_path}")
        return

    write_policy_yaml(source_path, output_path)
    print(f"generated: {output_path}")


def render_policy_yaml(source_path: Path) -> str:
    policy = load_policy_from_markdown(source_path)
    return "# Generated from contribution.md. Do not edit by hand.\n" + dump_yaml(policy)


def write_policy_yaml(source_path: Path, output_path: Path | None = None) -> Path:
    output_path = output_path or source_path.with_name("policy.yaml")
    rendered = render_policy_yaml(source_path)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


def _select_config_dirs(config: str | None, all_configs: bool) -> list[Path]:
    if config and all_configs:
        raise PolicyParseError("Pass either a config directory or --all, not both.")
    if all_configs:
        root = Path("configs")
        if not root.exists():
            raise PolicyParseError("configs directory does not exist.")
        return sorted(path for path in root.iterdir() if (path / "contribution.md").exists())
    if not config:
        raise PolicyParseError("Pass a config directory or --all.")
    return [Path(config)]


if __name__ == "__main__":
    raise SystemExit(main())
