# feat/sync-merge-ancestor

<!-- git-guard: ref=refs/heads/feat/sync-merge-ancestor -->

- Relax sync merge enforcement to allow sources that already contain the current target head.
- Disable runtime auto-sync in this source repository so hooks do not reinstall stale global runtime code.
