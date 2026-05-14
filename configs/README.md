# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.

Optional files:

- `policy.yaml`: generated review snapshot from `contribution.md`.

Maintain `contribution.md` by hand. The installer parses it directly and writes the runtime policy into the target repository.
