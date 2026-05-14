# TODO

Most of the original bootstrap TODO is now implemented:

- Mermaid `gitGraph` parsing for the supported branch/checkout/merge DSL.
- Automatic runtime policy generation from `configs/*/contribution.md` during install.
- Packaged Python CLI via `git-flow-guard`.
- `reference-transaction` hook runtime.
- Repo-local hook installation under `.git-flow-guard/`.
- `worktree`, `local`, and `global` `core.hooksPath` scopes.
- Config-owned integration tests under `configs/*/test_case.py`.
- Docker-based hook behavior tests under `test_env/`.

Remaining useful work:

- Add focused unit tests for the Mermaid parser and policy generator.
- Add package build verification in CI, including isolated `uv tool install --editable .` or venv-based editable install.
- Add an uninstall command to remove `.git-flow-guard/` and unset `core.hooksPath`.
- Add a command that prints the resolved policy for a config without writing files.
- Decide whether generated `policy.yaml` should remain checked in as a review artifact.
