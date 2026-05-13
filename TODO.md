# TODO: Mermaid GitGraph To Reference Transaction Guard

## Goal

Build a generic workflow guard that uses a restricted Mermaid `gitGraph` in `CONTRIBUTING.md` as the source of truth for local Git ref update policy.

The runtime target is Git's native `reference-transaction` hook. The hook should validate branch pointer updates before invalid local refs land.

## Source DSL

- Parse one `gitGraph TB:` fenced block from `CONTRIBUTING.md`.
- Support this minimal statement set:
  - `branch NAME`
  - `checkout NAME`
  - `merge NAME id:"SOURCE -> TARGET"`
  - `commit id:"..." tag:"..."`
- Require wildcard branch families to be quoted, for example `"infra/*"`.
- Treat `branch X` after `checkout Y` as an allowed branch-from edge.
- Treat `checkout TARGET` followed by `merge SOURCE` as a required merge/containment edge: `SOURCE -> TARGET`.
- Treat a source merged to multiple targets as a multi-target integration requirement.
- Treat tag commits after multi-target merges as release confirmation points.

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

- Keep `skills/git-flow-policy-writer/SKILL.md` focused on authoring compatible `CONTRIBUTING.md` gitGraph docs.
- Do not put runtime implementation details into the skill unless they affect the authoring rules.

## Tests

- Parser tests for quoted wildcard branch families.
- Parser tests for branch-from and merge-to edges.
- Policy generation tests for:
  - `infra/* -> dev`
  - `feat/* -> dev`
  - `release/* -> main` and `release/* -> dev`
  - `hotfix/* -> main` and `hotfix/* -> dev`
- Hook integration tests in a temporary Git repo:
  - reject illegal `main <- dev`
  - allow source family updates that satisfy the generated policy
  - require release/hotfix source containment in all required targets before tag confirmation
