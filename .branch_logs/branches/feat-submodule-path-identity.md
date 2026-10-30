# feat/submodule-path-identity

<!-- git-guard: ref=refs/heads/feat/submodule-path-identity -->

- Branch from dev `7bfe3a7d471df44d79f1e1d6d30eae78c42522ab` in an independent worktree; leave the globally editable dev checkout and all stable refs/tags unchanged.
- Resolve target gitlink paths via committed `.gitmodules` module names and the current worktree's registered Git directory. Reject missing/ambiguous mappings, parent discovery and unrelated Git directories without weakening fixed-object or allowed-ancestry checks.
- Add behavioral regressions using real temporary Git repositories and the official installer, including punctuated module names, linked-superproject private Git directories, legal relocation, missing repositories/objects/refs, disallowed ancestry and transaction mapping isolation.
- Document the supported same-name relocation and fail-closed resolution boundary. Project deployment is separate from global tool installation.
- Verification: `python3 -m unittest discover -s tests -p test_submodule_policy.py -v` passed all 14 real-Git regressions, including installed-hook branch acceptance/rejection (2026-10-28).
- Preserve Git's supported old-form embedded `.git` layout as observed in voxMani main: require its exact embedded Git directory and Git-reported superproject identity. Keep foreign separate Git-directory gitfiles rejected. Both affected real-Git installed-hook regressions passed; the original 14-case run is not represented as rerun.
