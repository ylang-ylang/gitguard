# feat/release-api-fix

<!-- git-guard: ref=refs/heads/feat/release-api-fix -->

- Use the GitHub Actions JSON API Accept type for artifact ZIP redirects; keep octet-stream for Release asset downloads. Live download of the original V1.18 build artifact succeeds with its original ZIP digest verified.
- Canonicalize automated tap ownership messages without a trailing newline because the real GitHub Git API trims it; make the stateful API test model reproduce that behavior.
- Capture release-controller code from dev, as explicitly approved, while still requiring the package's immutable version tag to resolve within remote main history. V1.18 and its source commit remain untouched.
- All 34 release/tap regression tests and workflow syntax validation pass after the fixes.
