#!/usr/bin/env bash
set -eu
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
git config --local core.hooksPath .git-guard/hooks
printf "%s\n" "enabled git-guard hooks for repository $repo_root"
printf "%s\n" "core.hooksPath=.git-guard/hooks"
