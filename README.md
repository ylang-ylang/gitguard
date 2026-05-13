# Git Flow Guard

Temporary development repository for a Mermaid `gitGraph` to Git `reference-transaction` hook policy workflow.

Current focus:

- Treat a restricted `CONTRIBUTING.md` Mermaid `gitGraph` as the human-authored policy source.
- Parse branch-family edges such as `"infra/*" -> dev`.
- Generate policy that can drive a local `reference-transaction` hook.
- Provide a Codex skill for writing compatible `CONTRIBUTING.md` gitGraph documents.

The first bundled skill lives at:

```text
skills/git-flow-policy-writer/SKILL.md
```
