# TODO

Most of the original bootstrap TODO is now implemented:

- Mermaid `gitGraph` parsing for the supported branch/checkout/merge DSL.
- Automatic runtime policy generation from `configs/*/contribution.md` during install.
- Packaged Python CLI via `git-guard`.
- `reference-transaction` hook runtime.
- Repo-local hook installation under `.git-guard/`.
- `worktree`, `local`, and `global` `core.hooksPath` scopes.
- Config-owned integration tests under `configs/*/test_case.py`.
- Docker-based hook behavior tests under `test_env/`.

Remaining useful work:

- Add focused unit tests for the Mermaid parser and policy generator.
- Add package build verification in CI, including isolated `uv tool install --editable .` or venv-based editable install.
- Add an uninstall command to remove `.git-guard/` and unset `core.hooksPath`.
- Add a command that prints the resolved policy for a config without writing files.
- Add a CI check that verifies `.git-guard/policy.json` is current with `.git-guard/contribution.md`.
