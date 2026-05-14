# Test Env

This directory contains a Docker image and one batch runner for testing Git hook behavior in a clean runtime.

The first smoke test verifies Git's native `reference-transaction` hook lifecycle:

- normal commits trigger branch ref updates;
- branch creation triggers branch ref updates;
- fast-forward merge updates the target branch ref;
- tag creation triggers tag ref updates;
- a non-zero exit from the hook during `prepared` rejects the ref update.

Build the image from the repository root:

```bash
mkdir -p .tmp
docker compose build policy-hook-tests
```

Run the policy hook integration tests with only `.tmp` mounted:

```bash
mkdir -p .tmp
docker compose run --rm policy-hook-tests
```

The image does not copy repository code. Compose bind-mounts the repository read-only at `/workspace` and mounts `.tmp` read-write at `/workspace/.tmp`. Runtime test repositories and logs are written only to `.tmp`.

`run_policy_hook_tests.py` only discovers `configs/*/test_case.py` files and runs each exported `TEST_CASE`. The shared base class is `configs/test_base.py`; each config owns its policy-specific DAG construction and rejection cases.

The policy tests install hooks through the package CLI:

```bash
PYTHONPATH=src python -m git_flow_guard.cli install --repo .tmp/<config_name> --config <config_name> --scope worktree
```

If you installed the isolated uv tool, use `git-flow-guard` in place of `PYTHONPATH=src python -m git_flow_guard.cli`.

The installed contribution document, enable script, hook wrapper, runtime `policy.json`, and Python runner live inside each generated test repository under `.git-flow-guard/`. This keeps the generated repo usable from both Docker and the host, because `core.hooksPath` does not point at `/workspace/test_env/...`.

The generated `enable.sh` is intentionally small: it only enables the checked-in hook for the current worktree by setting `core.hooksPath=.git-flow-guard/hooks`.

Hook rejection messages must include a policy hint that points at the generated repo's local Markdown file:

```text
git-flow-guard: see policy: <repo>/.git-flow-guard/contribution.md
```

The rejection reason itself uses a stable `CODE key=value` format so tests and agents can match exact failure classes without parsing friendly prose.

Each config gets a separate test directory:

```text
.tmp/<config_name>
```

For example:

```text
.tmp/basic-feature-release
.tmp/infra-feat-release
```

Each config test repo is built by a config-specific test class that inherits from the shared policy hook test base:

1. Create a complete valid example Git DAG for that config, including configured branch families, required merges, and tags.
2. Add one visible start symbol on the protected integration branch with this merge commit message:

```text
=========== GIT FLOW GUARD REJECTION TESTS START ===========
```

The branch used to introduce the marker has a plain commit message, so `git log --graph --all` shows only one visible start symbol.

After that separator, rejection cases are run against the same repo. Each rejection case snapshots `refs/heads/*` and `refs/tags/*` before and after the rejected Git command. The test fails if any branch or tag ref is created, deleted, or moved. If all rejection cases pass, the test prints a finish line without writing any new Git commit or ref:

```text
========test finished========
```

If a command that should be rejected is unexpectedly accepted, the test writes a visible failure marker commit before failing:

```text
!!!!!!!! GIT FLOW GUARD EXPECTED REJECTION WAS ACCEPTED !!!!!!!!
```

The policy integration tests cover:

- rejecting branch names outside the configured long-lived branches and branch families;
- rejecting branch creation from the wrong source branch;
- rejecting merge direction that is not present in the generated policy;
- rejecting tag names that do not match any generated tag rule;
- rejecting a release commit tagged with a hotfix-style tag;
- rejecting hotfix tags that change the base release line's major or minor component;
- rejecting multi-target source branches when they are merged to targets out of policy order;
- accepting the normal configured branch families, required merge targets, and tag rules before the separator.

Not yet covered by the generated Mermaid policy alone:

- full SemVer support such as prerelease/build metadata;
- server-side race protection for two concurrent processes creating the same next version tag.

Git may pass pseudo refs such as `HEAD`, `ORIG_HEAD`, or `AUTO_MERGE` through the hook. Runtime policy checks should filter to the configured policy refs, for example `refs/heads/*` and `refs/tags/*`.

The original low-level smoke test is still available:

```bash
docker compose run --rm reference-transaction-smoke
```
