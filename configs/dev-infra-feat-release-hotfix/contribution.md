# Infra/Feature Release Flow

```mermaid
gitGraph TB:
    commit id:"init"
    branch dev
    checkout dev
    commit id:"dev baseline"

    %% Cross-module infrastructure work
    branch "infra/*"
    checkout "infra/*"
    commit id:"infra work"

    checkout dev
    commit id:"dev advances during infra work"

    checkout "infra/*"
    commit id:"infra validation"
    merge dev id:"dev to infra/* sync"

    checkout dev
    merge "infra/*" id:"infra/* to dev"

    %% Single-module feature work
    checkout dev
    branch "feat/*"
    checkout "feat/*"
    commit id:"feature work"

    checkout dev
    commit id:"dev advances during feature work"

    checkout "feat/*"
    merge dev id:"dev to feat/* sync"

    checkout dev
    merge "feat/*" id:"feat/* to dev"

    %% Release from dev to main
    branch "release/*"
    checkout "release/*"
    commit id:"release hardening"
    commit id:"integration test"
    commit id:"regression test pass"
    checkout dev
    merge "release/*" id:"release/* to dev"
    checkout main
    merge "release/*" id:"release/* to main" tag:"v#.#.0"

    %% Hotfix from main
    checkout main
    branch "hotfix/*"
    checkout "hotfix/*"
    commit id:"hotfix"
    checkout dev
    merge "hotfix/*" id:"hotfix/* to dev"
    checkout main
    merge "hotfix/*" id:"hotfix/* to main" tag:"v=.=.#"
```

## Rules

- `infra/*` and `feat/*` branch from `dev`, must absorb the current `dev`, and merge to `dev`.
- `release/*` branches from `dev`, must merge to `dev`, then merge to `main`; the `main` merge result must be tagged with `v#.#.0`.
- `hotfix/*` branches from `main`, must merge to `dev`, then merge to `main`; the `main` merge result must be tagged with `v=.=.#`.
- `#` in tag patterns means one or more decimal digits.
- `=` in tag patterns means the same numeric component as the base release tag for this source branch.
