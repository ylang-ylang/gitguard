# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.
- `test_case.py`: config-specific integration tests.

Maintain `contribution.md` by hand. The installer parses it and writes `.git-flow-guard/policy.json` into the target repository for the hook runtime.

Current configs:

- `basic-feature-release`: `main`, `dev`, `feat/*`, `release/*`, `hotfix/*`.
- `dev-only`: `main`, `dev`, and `release/*`; `dev` allows direct commits, `main` only accepts tagged release merges.
- `infra-feat-release`: `main`, `dev`, `infra/*`, `feat/*`, `release/*`, `hotfix/*`.
