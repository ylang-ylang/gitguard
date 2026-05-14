from __future__ import annotations

import argparse
import sys

from git_flow_guard import __version__
from git_flow_guard import generate as generate_module
from git_flow_guard import install as install_module


def main() -> int:
    parser = argparse.ArgumentParser(prog="git-flow-guard")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install hooks into a Git repository.")
    install_parser.add_argument("--repo", default=".", help="Target Git repository working tree. Default: current directory.")
    install_parser.add_argument(
        "--config",
        required=True,
        help="Config name under configs/, config directory containing contribution.md, or direct contribution.md path.",
    )
    install_parser.add_argument(
        "--scope",
        choices=install_module.VALID_SCOPES,
        default="worktree",
        help="Where to write Git core.hooksPath: worktree, local, or global. Default: worktree.",
    )

    generate_parser = subparsers.add_parser("generate", help="Generate policy.yaml from contribution.md.")
    generate_parser.add_argument(
        "config",
        nargs="?",
        help="Config directory containing contribution.md. Omit with --all.",
    )
    generate_parser.add_argument(
        "--all",
        action="store_true",
        help="Generate policy.yaml for each configs/* directory that contains contribution.md.",
    )
    generate_parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if generated policy.yaml differs from the file on disk.",
    )

    args = parser.parse_args()
    if args.command == "install":
        return install_module.run_install_command(repo=args.repo, config=args.config, scope=args.scope, parser=parser)
    if args.command == "generate":
        return generate_module.run_generate_command(config=args.config, all_configs=args.all, check=args.check, parser=parser)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
