# feat/case-long-lived-origins

<!-- git-guard: ref=refs/heads/feat/case-long-lived-origins -->

- Expand case/*/* creation origins to all declared long-lived branches in policy compilation; bundled case policies now allow both main and dev.
- Validate every alternative source tip and record the matching source in branch_bases; preserve deterministic same-SHA ties and non-case behavior.
- Keep case-to-feature-only merge rules and linked-worktree branch-creation restrictions unchanged. Update all three case policy documents, integration scenarios, runtime mirrors and this worktree's generated policy.
- Verification: 14 new origin regressions, all 52 unit/packaging/release tests, and real Git hook integration runs for all four configurations passed.
- No other project's installed policy or published release was modified.
- Prepare the user-requested V1.19 release with package and CLI version 1.19; preserve V1.18 and all previous release assets.
