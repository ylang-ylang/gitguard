# feat/case-submodule-integration

<!-- git-guard: ref=refs/heads/feat/case-submodule-integration -->

- 2026-10-30: Apply the exact combined source/docs/regression changes from f7af2978a93f8f184eb15ccfdefe1627fbcaef65 and 4dbc6c51971916bd6768ea49fc2a9dbfc859be16 onto dev 99bcde74b55083b05c8bbc47f7dfe69549d9454a, retaining declared long-lived case origins from 88d2dec. A direct feat-to-feat merge was rejected by normal guard and aborted; equivalent cherry-picks preserve protected branch workflow without changing dev/main or the global editable binding.
- Preserve registered module-name/path resolution, exact embedded/absorbed repository identity, missing-object rejection and allowed ancestry; case merges remain feature-only. submodule_policy.py SHA256 remains 55cf9ef3a5ef2bdc44a477280b9b228468bf7837fba9d271e5c04f7bf3f5b06a, identical to 4dbc6c5.
- No build, tests, lint or formatter run. The voxMani assignment performs installed-hook positive/negative and producer-source identity checks only.
