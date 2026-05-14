# TODO: Mermaid GitGraph To Reference Transaction Guard

## Goal

Build a generic workflow guard that uses a restricted Mermaid `gitGraph` in `CONTRIBUTING.md` as the source of truth for local Git ref update policy.

The runtime target is Git's native `reference-transaction` hook. The hook should validate branch pointer updates before invalid local refs land.

## Source DSL

- Parse one `gitGraph TB:` fenced block from `CONTRIBUTING.md`.
- Support this minimal statement set:
  - `branch NAME`
  - `checkout NAME`
  - `merge NAME id:"SOURCE to TARGET" tag:"..."`
- Require wildcard branch families to be quoted, for example `"infra/*"`.
- Treat `branch X` after `checkout Y` as an allowed branch-from edge.
- Treat `checkout TARGET` followed by `merge SOURCE` as a required merge/containment edge: `SOURCE to TARGET`.
- Treat a source merged to multiple targets as a multi-target integration requirement.
- Treat tags on `main` merge statements as release confirmation points.
- Parse `tag:"v#.#.0"` and `tag:"v=.=.#"` on merge statements as numeric tag policies from the graph itself.
- Interpret `#` as one or more decimal digits.
- Interpret `=` as the same numeric component as the base release tag for the source branch.

## Generated Policy

- Generate a machine-readable policy file from the gitGraph.
- First policy scope:
  - protected targets: `refs/heads/main`, `refs/heads/dev`
  - source families: `refs/heads/infra/`, `refs/heads/feat/`, `refs/heads/release/`, `refs/heads/hotfix/`
- Do not require target HEAD equality. Require target refs to contain the source commit.
- Ignore non-policy refs unless explicitly configured.

## Hook Runtime

- Install a worktree-scoped hook path:

```bash
git config extensions.worktreeConfig true
git config --worktree core.hooksPath .githooks
```

- Provide `.githooks/reference-transaction` as a thin wrapper around a Python checker.
- Enforce only during the blocking `prepared` phase.
- Read ref updates from stdin:

```text
<old-sha> <new-sha> <ref-name>
```

## Skill

- Keep `.codex/skills/git-flow-policy-writer/SKILL.md` focused on authoring compatible `CONTRIBUTING.md` gitGraph docs.
- Do not put runtime implementation details into the skill unless they affect the authoring rules.

## Tests

- Parser tests for quoted wildcard branch families.
- Parser tests for branch-from and merge-to edges.
- Policy generation tests for:
  - `infra/* to dev`
  - `feat/* to dev`
  - `release/* to main` and `release/* to dev`
  - `hotfix/* to main` and `hotfix/* to dev`
- Hook integration tests in a temporary Git repo:
  - reject illegal `main <- dev`
  - allow source family updates that satisfy the generated policy
  - require release/hotfix source containment in all required targets before tagged main merge confirmation
  - reject release tags that do not match `v#.#.0`
  - reject hotfix tags that do not match `v=.=.#`
