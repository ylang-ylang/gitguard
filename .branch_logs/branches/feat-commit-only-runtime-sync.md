# feat/commit-only-runtime-sync

<!-- git-guard: ref=refs/heads/feat/commit-only-runtime-sync -->

- Limit runtime auto-sync to ordinary pre-commit hooks.
- Keep ref update, merge commit, and push hooks from writing the worktree.
