---
name: git-flow-policy-writer
description: Write or revise CONTRIBUTING.md Mermaid gitGraph policy DSLs for Git branch flow rules that will be parsed into reference-transaction hook policy. Use when asked to create branch workflow docs, normalize gitGraph branch families such as infra/* or release/*, or keep Git flow docs machine-readable for local ref update guards.
---

# Git Flow Policy Writer

## Purpose

Write `CONTRIBUTING.md` branch workflow docs where the Mermaid `gitGraph` is a restricted policy DSL, not just an illustration. The graph should be readable by humans and parseable into rules for `reference-transaction` hooks.

## DSL Rules

- Use one Mermaid fenced block with `gitGraph TB:`.
- Treat `main` and `dev` as literal long-lived branches.
- Represent branch families directly as quoted wildcard branches:
  - `"infra/*"`
  - `"feat/*"`
  - `"release/*"`
  - `"hotfix/*"`
- Do not use concrete examples such as `infra/sensor-driver` when the policy means all `infra/*`.
- Always quote wildcard branch names in gitGraph statements: `branch "infra/*"`, `checkout "infra/*"`, `merge "infra/*"`.
- Interpret `branch X` after `checkout Y` as `Y` may branch to `X`.
- Interpret `checkout TARGET` followed by `merge "SOURCE"` as `SOURCE` must be allowed to merge into `TARGET`.
- Write merge ids in exact machine-friendly form: `id:"SOURCE -> TARGET"`, for example `id:"release/* -> main"`.
- Do not encode special semantics in labels such as `back-merge`. If a source family must land in multiple targets, express that by merging it into each target in the graph.
- If a source family merges into more than one target, treat those targets as required containment targets for the same source family.
- Place release/hotfix tag commits after all required target merges, on `main`.

## Preferred Graph Shape

Use this shape unless the user gives different branch families:

```mermaid
gitGraph TB:
    commit id:"init"
    branch dev
    checkout dev
    commit id:"dev baseline"

    branch "infra/*"
    checkout "infra/*"
    commit id:"infra work"
    checkout dev
    merge "infra/*" id:"infra/* -> dev"

    branch "feat/*"
    checkout "feat/*"
    commit id:"feature work"
    checkout dev
    merge "feat/*" id:"feat/* -> dev"

    branch "release/*"
    checkout "release/*"
    commit id:"release hardening"
    checkout main
    merge "release/*" id:"release/* -> main"
    checkout dev
    merge "release/*" id:"release/* -> dev"
    checkout main
    commit id:"vX.Y.0" tag:"vX.Y.0"

    checkout main
    branch "hotfix/*"
    checkout "hotfix/*"
    commit id:"hotfix"
    checkout main
    merge "hotfix/*" id:"hotfix/* -> main"
    checkout dev
    merge "hotfix/*" id:"hotfix/* -> dev"
    checkout main
    commit id:"vX.Y.Z" tag:"vX.Y.Z"
```

## Validation Checklist

- Confirm every wildcard branch family is quoted in `gitGraph`.
- Confirm every `merge` has an `id:"SOURCE -> TARGET"` label that matches the merge statement.
- Confirm `release/*` and `hotfix/*` merge into both `main` and `dev` before their tag commit.
- Confirm there are no example-only branch names if the policy is intended to cover a full family.
- Confirm the accompanying prose and any flowchart do not contradict the `gitGraph`.
