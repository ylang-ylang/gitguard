from __future__ import annotations

import argparse
import sys

from git_flow_guard import __version__
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

    args = parser.parse_args()
    if args.command == "install":
        return install_module.run_install_command(repo=args.repo, config=args.config, scope=args.scope, parser=parser)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
