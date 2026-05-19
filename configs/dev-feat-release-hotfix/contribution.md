# Basic Feature Release Flow

```mermaid
gitGraph TB:
    commit id:"init"
    branch dev
    checkout dev
    commit id:"dev baseline"

    %% Feature work
    branch "feat/*"
    checkout "feat/*"
    commit id:"feature work"
    checkout dev
    merge "feat/*" id:"feat/* to dev"

    %% Release from dev to main
    branch "release/*"
    checkout "release/*"
    commit id:"release hardening"
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

- `feat/*` branches from `dev` and merges to `dev`.
- `release/*` branches from `dev`, must merge to `dev`, then merge to `main` with `v#.#.0`.
- `hotfix/*` branches from `main`, must merge to `dev`, then merge to `main` with `v=.=.#`.
- `#` in tag patterns means one or more decimal digits.
- `=` in tag patterns means the same numeric component as the base release tag for this source branch.
