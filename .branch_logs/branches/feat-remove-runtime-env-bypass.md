# feat/remove-runtime-env-bypass

<!-- git-guard: ref=refs/heads/feat/remove-runtime-env-bypass -->

- Remove environment-variable sentinels that could alter hook runtime behavior.
- Keep runtime auto-sync and internal tag pushes safe through hook/runtime implementation instead of user-injectable env.
- Add regression coverage that legacy env names no longer bypass runtime repair or pre-push tag conflict checks.
