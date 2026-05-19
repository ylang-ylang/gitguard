# Direct Dev Release Flow

```mermaid
gitGraph TB:
    commit id:"init"
    branch dev
    checkout dev
    commit id:"dev baseline"
    checkout main
    merge dev id:"dev to main"

    %% Release from dev to main
    checkout dev
    branch "release/*"
    checkout "release/*"
    commit id:"release hardening"
    checkout main
    merge "release/*" id:"release/* to main" tag:"V#.#"
```

## Rules

- `dev` may merge directly to `main`.
- `release/*` branches from `dev`.
- `release/*` releases must merge to `main` with a `V#.#` tag.
- A missing release tag blocks later `release/* to main` merges, but does not block allowed `dev to main` merges.
