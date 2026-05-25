# Dev Only Flow

```mermaid
gitGraph TB:
    commit id:"init"

    checkout main
    branch dev order: 1
    checkout main
    commit id:"main after dev fork"

    checkout dev
    commit id:"dev work"

    checkout main
    merge dev id:"dev to main"

    checkout dev
    commit id:"tagged dev work"
    checkout main
    merge dev id:"dev to main tagged" tag:"V#.#"
```

## Rules

- `dev` is the only development branch and may receive direct commits.
- `main` may only receive merges from `dev`.
- The repeated `dev` to `main` merge declares that `dev` may merge to `main` with or without a `V#.#` tag.
- `V#.#` tags are optional. If created, they must point to a `main` commit produced by the `dev` to `main` merge history.
- `main` must not receive direct commits.
- Ad hoc tags are not allowed; release tags are allowed only when they satisfy the optional `dev` to `main` tag rule.
