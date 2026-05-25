# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.
- `test_case.py`: config-specific integration tests.

Maintain `contribution.md` by hand. The installer parses it and writes `.git-guard/policy.json` into the target repository for the hook runtime.

Current configs:

- `dev-only`: `main` and `dev`; direct `dev` work merges to `main`, with an optional `V#.#` release tag.
- `dev-feat-case`: `main`, `dev`, `feat/*`, and `case/*/*`; case work branches from `main`, feeds reusable work into feature branches, and `main` only accepts tagged `dev` releases.
- `dev-feat-release-hotfix-case`: `main`, `dev`, `feat/*`, `release/*`, `hotfix/*`, `case/*/*`; case work branches from `main`, feeds reusable work into feature branches, and feature work absorbs `dev` before merging into `dev`.
- `dev-infra-feat-release-hotfix-case`: `main`, `dev`, `infra/*`, `feat/*`, `release/*`, `hotfix/*`, `case/*/*`; case work branches from `main`, feeds reusable work into feature branches, and infra and feature work absorb `dev` before merging into `dev`.
