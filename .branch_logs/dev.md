# dev

<!-- git-guard: ref=refs/heads/dev -->

- Merge feat/per-branch-logs to enable per-branch directory branch logs.
- Merge feat/sync-merge-ancestor to relax sync merge ancestry enforcement and disable source runtime auto-sync.
- Merge feat/require-managed-files-staged to keep runtime auto-sync off protected branches and require staged managed hook files.
- Merge feat/commit-only-runtime-sync to prevent non-commit hooks from writing runtime sync changes.
- Merge feat/clean-nested-git-env to isolate nested runtime Git commands from parent hook env and run policy tests from linked worktrees.
- Merge feat/remove-runtime-env-bypass to remove user-injectable hook runtime sentinel env variables.
- merge feat/runtime-hardening: runtime hardening, trigger log, auto-push
- merge feat/docs-protected-workflow: document protected-branch workflow
- Merge feat/homebrew-distribution: packaged policies/runtime, exact-tag release validation, immutable source artifacts and checked Homebrew Formula distribution for 1.18.
