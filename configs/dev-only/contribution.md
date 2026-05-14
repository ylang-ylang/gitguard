# Dev Only Release Flow

```mermaid
gitGraph TB:
    commit id:"init"
    branch dev
    checkout dev
    commit id:"dev baseline"
    commit id:"direct dev work"

    %% Release from dev to main
    branch "release/*"
    checkout "release/*"
    commit id:"release hardening"
    checkout main
    merge "release/*" id:"release/* to main" tag:"V#.#"
```

## Rules

- `dev` is the only development branch and may receive direct commits.
- `release/*` branches from `dev`.
- `release/*` is the only branch family allowed to merge into `main`.
- `release/*` releases must use a `V#.#` tag, where `#` means one or more decimal digits.
- `main` must not receive direct commits.
- Ad hoc tags on `main` are not allowed; release tags are allowed only when they satisfy the `release/* to main` rule.
