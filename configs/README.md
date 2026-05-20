# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.
- `test_case.py`: config-specific integration tests.

Maintain `contribution.md` by hand. The installer parses it and writes `.git-flow-guard/policy.json` into the target repository for the hook runtime.

Current configs:

- `dev-only`: `main` and `dev`; `dev` allows direct commits, `main` accepts `dev`, and `V#.#` tags are optional but validated when present.
- `dev-release`: `main`, `dev`, and `release/*`; `dev` may merge to `main`, while release fixes merge back to `dev` before tagged release merges.
- `dev-feat-release-hotfix`: `main`, `dev`, `feat/*`, `release/*`, `hotfix/*`.
- `dev-infra-feat-release-hotfix`: `main`, `dev`, `infra/*`, `feat/*`, `release/*`, `hotfix/*`.
