# P3 — layered Agent Memory opening index

## Design decisions

- Opening visibility is a separate `tier` axis; it does not replace the existing
  `raw` / `validated` / `promoted` / `superseded` lifecycle.
- An explicit `tier: core|archive` wins. Otherwise `validated` and `promoted` are
  core candidates, `raw` and `superseded` are archive, `user` and `feedback`
  types are core, and anything not established by those signals defaults to
  archive. The default is counted and reported as `tier_default_archive`.
- `agent-memory lifecycle <document> --tier core|archive` is the only operation
  that writes an explicit tier. It edits only that note's top-level `tier` field,
  preserves lifecycle and unknown frontmatter, and then refreshes the derived
  SQLite index. Ordinary reads do not edit Markdown or settings.
- `brief` samples core only. `context` samples both tiers with an 8 KiB budget;
  search remains the point lookup. Counts always describe the complete visible
  scope, so `distinct = core + archive` even when brief shows only a few routes.
- One physical note visible through both project and shared roots occupies one
  project-first route slot. Empty-core output preserves registration guidance,
  offers context, and only prints a tier command when it has a real document ID.
- Old read-only indexes without the new `memory_meta.tier` column continue to
  infer tiers. A writable sync adds the cache column; no bulk memory migration is
  required.

## Red evidence

The new public-seam tests were copied onto baseline `061bf6e` before the
implementation was applied.

```text
$ python3 -m unittest discover -s eval/agent-memory/tests -p 'test_arch_brief.py'
ERROR: test_brief_classifies_mixed_metadata_conservatively
KeyError: 'core'
ERROR: test_context_expands_all_tiers_beyond_brief_candidates
KeyError: 'core'
ERROR: test_empty_core_brief_gives_context_and_lifecycle_commands
KeyError: 'core'
ERROR: test_project_shared_overlap_deduplicates_and_backfills_tagged_routes
KeyError: 'core'
ERROR: test_unregistered_archive_keeps_registration_and_tier_route
KeyError: 'core'
FAIL: test_empty_and_unregistered_are_distinct
FAIL: test_explicit_tier_is_indexed_and_legacy_readonly_index_infers
Ran 14 tests in 0.688s
FAILED (failures=2, errors=5)
brief_rc=1

$ python3 -m unittest discover -s eval/agent-memory/tests -p 'test_roadmap.py'
ERROR: test_cli_tier_override_preserves_lifecycle_and_all_other_frontmatter_bytes
argparse.ArgumentError: argument status: invalid choice: 'core'
Ran 5 tests in 0.043s
FAILED (errors=1)
roadmap_rc=1
```

The adversarial formatter test also exposed a non-terminating trim loop in the
first implementation attempt; that run was interrupted with rc 130. The final
formatter has a monotonic pruning path and a minimal bounded fallback.

## Green evidence

Agent Memory suite:

```text
$ python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py'
Ran 94 tests in 2.822s
OK (skipped=1)
```

The skip is the existing optional PyYAML round-trip check. The fixed opening
query fixture remains `actionable=8/8`, `dead_end=0`:

```text
$ python3 -m unittest eval.agent-memory.tests.test_arch_integration.AgentMemoryArchitectureIntegrationTests.test_opening_query_acceptance_and_synthetic_all_miss_fixture
Ran 1 test in 0.453s
OK
```

Pinned 523-document retrieval corpus:

```text
corpus_sha256=150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c
before input_valid=true indexed=523
after  input_valid=true indexed=523
before metrics: recall@5=0.925 recall@10=0.95 mrr@10=0.8283333333333334
after  metrics: recall@5=0.925 recall@10=0.95 mrr@10=0.8283333333333334
phase 2: PASS
query_differences=[]
failures=[]
budget.assessed=true
```

`git diff --check` passed. `memory_cjk.py` and all four files under
`eval/agent-memory/retrieval/` have zero diff.

The repository-wide wrapper did not reach its test scripts because the host's
pnpm dependency preflight rejected ignored package build scripts:

```text
$ pnpm test
[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: esbuild@0.27.7, protobufjs@7.5.6
[ERROR] Command failed with exit code 1: ... pnpm.mjs install
```

No build-script approval or host policy change was made. The directly relevant
Python suite and frozen retrieval gate above completed independently.

## Real-library acceptance

Settings checksum before and after all reads/tests:

```text
6fd3e8f998ebc9adac011d4a95664d2f
```

Before P3, the real ARCP brief showed four recent audit/closure events first,
then two shared entries, while omitting 321 of 327 visible memories:

```text
counts: distinct=327 global=161 project=166
- [arcp] Mutation audit found exactly one weak assertion in five consecutive sweeps
- [arcp] The third axis closed: every adapter call now recovers its ambiguous outcome
- [arcp] Verify your own verification before you doubt the agent
- [arcp] Two axes closed by verified absence in one night
- [_shared] ARCP and Agent Server are separate current projects
- [_shared] feedback codex lane model luna high
omitted: 321
truncated: true
```

After P3, the same read-only index produces:

```text
status=ok project=arcp source=path
counts: distinct=327 global=161 project=166 core=79 archive=248 defaulted=124 (components may overlap)
diagnostics: resolved_memory_root, tier_default_archive(124)
- [arcp] Responsibility-loop entry points must authorize non-mutating outcomes and serialize takeover against the evaluated RunAttempt
- [arcp] ARCP Channel receipt evidence must be fenced to the current RuntimeGeneration
- [_shared] feedback codex lane model luna high
- [_shared] 当前 Agent 工作流与模型路由（2026-08-23）
omitted: 323
next: agent-memory context --project arcp
truncated: true
```

The new entries are better opening context because they are validated project
invariants and durable owner/workflow guidance, not reports of one audit run or
one completed axis. The counts stay honest about the 248 archived items and the
124 conservative defaults.

UTF-8 output sizes on the real library:

```text
brief:   2042 bytes (limit 2048)
context: 8090 bytes (limit 8192)
```

No real memory Markdown was modified. The only allowed write path is an
explicit user-issued `lifecycle ... --tier ...` command, covered with tests for
nested keys, comments, quoted values, and an untouched sibling note.

## HANDOFF

- Explicit tier overrides can be added gradually as useful memories are read;
  no one-time classification of the existing library is required.
- `validated` and `promoted` are intentionally core candidates per the P3 brief.
  If promoted provenance is noisy, set that note explicitly to archive.
- Invalid hand-authored tier values currently fall through to automatic
  inference; Doctor does not yet report them.
- Context is an all-tier, larger-budget route inventory (up to 20 entries), not
  a literal dump of all 327 memories. Search remains the unbounded targeted
  retrieval surface.
