# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.
- `test_case.py`: config-specific integration tests.

Maintain `contribution.md` by hand. The installer parses it and writes `.git-guard/policy.json` into the target repository for the hook runtime.

Current configs:

- `dev-only`: `main` and `dev`; direct `dev` work merges to `main`, with an optional `V#.#` release tag.
- `dev-feat`: `main`, `dev`, and `feat/*`; feature work absorbs `dev`, merges into `dev`, and `main` only accepts tagged `dev` releases.
- `dev-feat-release-hotfix`: `main`, `dev`, `feat/*`, `release/*`, `hotfix/*`; feature work absorbs `dev` before merging into `dev`.
- `dev-infra-feat-release-hotfix`: `main`, `dev`, `infra/*`, `feat/*`, `release/*`, `hotfix/*`; infra and feature work absorb `dev` before merging into `dev`.
