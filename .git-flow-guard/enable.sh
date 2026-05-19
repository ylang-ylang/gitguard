#!/usr/bin/env bash
set -eu
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
git config extensions.worktreeConfig true
git config --worktree core.hooksPath .git-flow-guard/hooks
printf "%s\n" "enabled git-flow-guard hooks for $repo_root"
printf "%s\n" "core.hooksPath=.git-flow-guard/hooks"
