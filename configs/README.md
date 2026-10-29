# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.
- `test_case.py`: config-specific integration tests.

Maintain `contribution.md` by hand. The installer parses it and writes `.git-guard/policy.json` into the target repository for the hook runtime.

For the `case/*/*` family, compilation expands the illustrative fork in the graph into alternative `branch_from` edges from every declared long-lived (non-wildcard) branch. All bundled case policies currently declare `main` and `dev`; a case branch must start at either branch's current tip, and its recorded base identifies the matching source. This does not change merge permissions: case work still merges only into `feat/*`.

Existing repositories must re-run `git-guard install --repo <repo> --config <config>` with the updated compiler to regenerate their installed `.git-guard/policy.json`; runtime auto-sync alone does not recompile policy. Select the bundled config name or the repository's own contribution document as appropriate. Upgrading this project does not mass-update other repositories.

Current configs:

- `dev-only`: `main` and `dev`; direct `dev` work merges to `main`, with an optional `V#.#` release tag.
- `dev-feat-case`: `main`, `dev`, `feat/*`, and `case/*/*`; case work branches from any declared long-lived branch (`main` or `dev`), feeds reusable work into feature branches, and `main` only accepts tagged `dev` releases.
- `dev-feat-release-hotfix-case`: `main`, `dev`, `feat/*`, `release/*`, `hotfix/*`, `case/*/*`; case work branches from any declared long-lived branch (`main` or `dev`), feeds reusable work into feature branches, and feature work absorbs `dev` before merging into `dev`.
- `dev-infra-feat-release-hotfix-case`: `main`, `dev`, `infra/*`, `feat/*`, `release/*`, `hotfix/*`, `case/*/*`; case work branches from any declared long-lived branch (`main` or `dev`), feeds reusable work into feature branches, and infra and feature work absorb `dev` before merging into `dev`.
