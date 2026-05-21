# Dev Feat Flow

```mermaid
gitGraph TB:
    commit id:"init"
    branch dev
    checkout dev
    branch "feat/*"
    checkout "feat/*"
    commit id:"feature work"

    checkout "feat/*"
    merge dev id:"dev to feat/* sync"

    checkout dev
    merge "feat/*" id:"feat/* to dev"

    checkout main
    merge dev id:"dev to main" tag:"V#.#"
```

## Rules

- `feat/*` branches from `dev`.
- `feat/*` work must absorb the current `dev` and merge back to `dev`.
- `dev` is the integration branch and must not receive direct commits after the policy is installed.
- `main` may only receive tagged merges from `dev`.
- `main` release merge results must use a `V#.#` tag, where `#` means one or more decimal digits.
- `main` must not receive direct commits.
- Ad hoc tags are not allowed; release tags are allowed only when they satisfy the `dev` to `main` rule.
