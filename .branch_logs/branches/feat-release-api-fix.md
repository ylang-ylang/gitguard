# feat/release-api-fix

<!-- git-guard: ref=refs/heads/feat/release-api-fix -->

- Use the GitHub Actions JSON API Accept type for artifact ZIP redirects; keep octet-stream for Release asset downloads. Live download of the original V1.18 build artifact succeeds with its original ZIP digest verified.
- Canonicalize automated tap ownership messages without a trailing newline because the real GitHub Git API trims it; make the stateful API test model reproduce that behavior.
- Capture release-controller code from dev, as explicitly approved, while still requiring the package's immutable version tag to resolve within remote main history. V1.18 and its source commit remain untouched.
- All 34 release/tap regression tests and workflow syntax validation pass after the fixes.
- Absorb the current dev integration before completing the draft-release API compatibility repair; preserve the existing V1.18 tag and original artifact anchor.
- Discover existing drafts through the authenticated release listing when the published-by-tag endpoint returns 404. The trusted metadata-only planner has the permission needed to see drafts; tagged source execution remains isolated with read-only permissions.
- Compare stable release and asset identities rather than download counters. All 38 package/release regressions pass; a read-only live probe locates the existing V1.18 draft and its original artifact/run anchor without changing it.
