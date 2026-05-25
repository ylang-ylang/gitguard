# feat/branch-log-auto-restore

- Define `branch_logs.force_required=true` as requiring a real staged branch-log change in ordinary commits.
- Keep merge commits normalized to `.branch_logs/.gitkeep` so source branch logs stay target-local.
