# Lane D — runtime usage documentation

## What changed

- Replaced the generic `${CLAUDE_PLUGIN_ROOT}` assumption with a first-screen runtime
  preflight: validate the known absolute loaded-skill directory, prefer a real
  `agent-memory` command, otherwise use the sibling Python entrypoint.
- Documented Python 3.10+, stdlib SQLite/FTS5, and the complete `scripts/` directory as
  runtime requirements. Codex/standalone discovery is explicitly not a PATH install.
- Documented state-specific bootstrap: missing settings means `init` then `sync`; a
  missing index means `sync` only; unrelated exit-2 failures must not trigger force init.
- Corrected doctor 0/1/2 handling, the stale write-DDL description, CJK query guidance,
  the search/capture `--path` asymmetry, empty-registry capture setup, settings overrides,
  exact capture-root behavior, and non-destructive corrupt-index recovery.
- Kept `${CLAUDE_PLUGIN_ROOT}` only in a labeled Claude-specific example and marked the
  actual Claude runtime substitution as not end-to-end verified here.

## Real evidence

Red reproduction before the documentation change:

```text
$ env -u CLAUDE_PLUGIN_ROOT bash -c '"${CLAUDE_PLUGIN_ROOT}/skills/agent-memory/scripts/setup.sh"'
bash: line 1: /skills/agent-memory/scripts/setup.sh: No such file or directory
exit=127
```

Generic preflight against this worktree selected the fallback, and both path and
requirements checks passed:

```text
selected=python3 /home/agent/am/d/skills/agent-memory/scripts/agent_memory.py
path_checks_exit=0
agent-memory requirements: ok
requirements_exit=0
help_exit=0 first=usage: agent-memory [-h] [--settings SETTINGS]
```

Fresh isolated HOME (`/tmp/am-lane-d.bratMJ`) produced the documented state sequence:

```text
fresh status: exit=2, settings not found
init: exit=0
post-init status: exit=2, index not initialized
sync: exit=0, indexed=0 removed=0 roots=0
final status: exit=0, documents=0
search x: exit=0, []
doctor: exit=0, status=ok, bindings=0
capture with no binding/root: exit=2, no project binding matches path
```

A full standalone skill copy with PATH containing Python but no Node, pnpm, or
`agent-memory` resolved and ran without the repository package:

```text
copy=/tmp/am-lane-d-nonode.vCKDyS
node_rc=1 pnpm_rc=1 agent_memory_rc=1
init=0
sync=0
status=0
search=0 output=[]
```

The path-contract fixture indexed two Markdown files under project `demo`:

```text
resolve --path /tmp/am-lane-d.bratMJ/project: exit=0, project=demo
search known phrase --path .../definitely-unbound: exit=0, one global result
capture --path .../definitely-unbound: exit=2, no project binding matches path
search 中文 against indexed 中文查询测试: exit=0, []
```

Adding Markdown after sync yielded doctor warning exit 1. The exact explicit-code pattern
then continued to an independently invoked search:

```text
doctor_direct_exit=1 status=warn warnings=1
agent-memory doctor reported warnings; review them
handled_doctor_exit=1 search_after_warning_exit=0 hits=1
```

Validation:

```text
$ python3 /home/agent/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/agent-memory
Skill is valid!
exit=0

$ python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py'
Ran 37 tests in 2.960s
OK
exit=0

$ git diff --check
exit=0
```

## Unresolved / not run

- Claude Code plugin substitution/discovery was not available for an end-to-end run. Only
  the example's resolved local path was checked to exist; the user docs say this plainly.
- Lane A's auxiliary Han index was not present in this worktree. The docs therefore state
  the measured current limitation and do not claim CJK support has landed.
- Lane C's setup change was still uncommitted in its separate worktree during this lane's
  verification. These docs do not claim that setup integration passed; the portable
  Python entrypoint was exercised directly.
- Read-only/WAL runtime behavior comes from the existing code/recon contract and is not
  claimed as a new Lane D runtime pass. Lane E must execute the post-integration matrix.

## HANDOFF

- Lane E: after merging A/B/C/D, execute the documented generic, standalone Codex-copy,
  and Claude-plugin paths literally. Record resolved entry paths and all exit codes. Mark
  Claude `NOT RUN` rather than treating a directory-layout simulation as runtime proof.
- Lane E: re-run the Han query after A lands and update the durable limitation wording only
  if the integrated implementation and explicit post-upgrade `sync` prove the actual route.
- Integrator: retain Lane D's four documentation files plus this report as a unit. If
  Lane A/B changes diagnostic field semantics, remove the older caveat about mislabelled
  `match_mode` in `doctor-sync-settings.md` during the owning-lane reconciliation rather
  than changing runtime code here.
