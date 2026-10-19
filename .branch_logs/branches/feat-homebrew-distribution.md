# feat/homebrew-distribution

<!-- git-guard: ref=refs/heads/feat/homebrew-distribution -->

- Package all four canonical Mermaid policies and copied runtime in noneditable setuptools distributions; make hook Python discovery portable outside a source checkout.
- Align the new package and CLI version to 1.18 for a future maintainer-created V1.18 tag; preserve historical tags and reject their mismatched 0.1.0 metadata rather than fabricate mappings.
- Add tag-driven, main-history-validated release jobs with isolated credentials, source execution, immutable draft recovery and published-asset verification.
- Add source-sdist Homebrew Formula rendering and dedicated-token Formula-only checked PR distribution to ylang-ylang/homebrew-tap.
- Merge the isolated tap-checks bootstrap via homebrew-tap PR #3 after its required check passes; preserve all existing Portmap checks and every Formula file.
- Configure the supplied dedicated credential as gitguard's HOMEBREW_TAP_TOKEN without printing or storing it in project files; never use a broad-credential fallback.
- Verify 35 packaging/release regression tests, all four policy integration suites, wheel/sdist isolated hook smoke, and real Brew install/test/reinstall/upgrade/uninstall in an isolated Linux x86_64 environment. The upgrade-only 1.19 fixture is not a published version or tag; repository configuration, logs, state, HEAD and index remain unchanged.
