# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.
- `policy.yaml`: generated output from `contribution.md`.

Regenerate all configs from the repository root:

```bash
git-flow-guard generate --all
```

Validate generated files without writing:

```bash
git-flow-guard generate --all --check
```
