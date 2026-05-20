# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.
- `test_case.py`: config-specific integration tests.

Maintain `contribution.md` by hand. The installer parses it and writes `.git-flow-guard/policy.json` into the target repository for the hook runtime.

Current configs:

- `dev-only`: `main` and `dev`; direct `dev` work merges to `main`, with an optional `V#.#` release tag.
- `dev-feat`: `main`, `dev`, and `feat/*`; feature work merges into `dev`, and `main` only accepts tagged `dev` releases.
- `dev-feat-release-hotfix`: `main`, `dev`, `feat/*`, `release/*`, `hotfix/*`.
- `dev-infra-feat-release-hotfix`: `main`, `dev`, `infra/*`, `feat/*`, `release/*`, `hotfix/*`.
