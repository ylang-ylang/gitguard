# Configs

Each subdirectory is an independent branch-flow policy config.

Required files:

- `contribution.md`: Mermaid `gitGraph` policy source.

Maintain `contribution.md` by hand. The installer parses it and writes `.git-flow-guard/policy.json` into the target repository for the hook runtime.
