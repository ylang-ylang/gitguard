# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.
- `test_case.py`: config-specific integration tests.

Maintain `contribution.md` by hand. The installer parses it and writes `.git-flow-guard/policy.json` into the target repository for the hook runtime.

Current configs:

- `dev-feat-release-hotfix`: `main`, `dev`, `feat/*`, `release/*`, `hotfix/*`.
- `dev-infra-feat-release-hotfix`: `main`, `dev`, `infra/*`, `feat/*`, `release/*`, `hotfix/*`.
- `dev-release`: `main`, `dev`, and `release/*`; `dev` allows direct commits, release fixes merge back to `dev`, and `main` only accepts tagged release merges.
- `dev-release_slack`: `main`, `dev`, and `release/*`; `dev` may merge to `main`, while release fixes merge back to `dev` before tagged release merges.
