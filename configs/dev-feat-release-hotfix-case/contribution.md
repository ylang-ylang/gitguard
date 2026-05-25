# Basic Feature Release Case Flow

```mermaid
gitGraph TB:
    commit id:"init"

    checkout main
    branch dev order: 1
    checkout main
    commit id:"main after dev fork"

    checkout dev
    commit id:"dev branch history"

    %% Feature work
    branch "feat/*" order: 2
    checkout main
    commit id:"main after feat fork"

    checkout main
    branch "case/*/*" order: 5
    checkout main
    commit id:"main after case fork"

    checkout "case/*/*"
    commit id:"case work"

    checkout "feat/*"
    merge "case/*/*" id:"case/*/* to feat/*"
    commit id:"feature work"

    checkout dev
    commit id:"dev branch sync point"

    checkout "feat/*"
    merge dev id:"dev to feat/* sync"

    checkout dev
    merge "feat/*" id:"feat/* to dev"

    %% Release from dev to main
    branch "release/*" order: 3
    checkout main
    commit id:"main after release fork"
    checkout "release/*"
    commit id:"release hardening"
    checkout dev
    merge "release/*" id:"release/* to dev"
    checkout main
    merge "release/*" id:"release/* to main" tag:"v#.#.0"

    %% Hotfix from main
    checkout main
    branch "hotfix/*" order: 4
    checkout main
    commit id:"main after hotfix fork"
    checkout "hotfix/*"
    commit id:"hotfix"
    checkout dev
    merge "hotfix/*" id:"hotfix/* to dev"
    checkout main
    merge "hotfix/*" id:"hotfix/* to main" tag:"v=.=.#"
```

## Rules

- `feat/*` branches from `dev`, must absorb the current `dev`, and merges to `dev`.
- `case/*/*` means `case/<context>/<topic>`, where `<context>` is a real project, customer, dataset, robot, deployment, or reproducible scenario.
- `case/*/*` branches from `main` and may merge only into `feat/*`.
- `case/*/*` must not merge directly into `dev` or `main`; reusable work must be distilled through `feat/*`.
- `release/*` branches from `dev`, must merge to `dev`, then merge to `main`; the `main` merge result must be tagged with `v#.#.0`.
- `hotfix/*` branches from `main`, must merge to `dev`, then merge to `main`; the `main` merge result must be tagged with `v=.=.#`.
- `dev` is the integration branch and must not receive direct commits after the policy is installed.
- `#` in tag patterns means one or more decimal digits.
- `=` in tag patterns means the same numeric component as the base release tag for this source branch.
