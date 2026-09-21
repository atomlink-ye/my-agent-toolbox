# P4 implementation lane

## Authority and design decisions

- Specification: `BRIEF.md`; base: `c4ff4aa5576df7cdcdb86060f73d3d5e9b1979ca`.
- Tier inference precedence is explicit valid `tier` > recognized lifecycle `status` > recognized `type` > archive default. Sources are reported as `explicit`, `status`, `type`, or `default` using that same precedence.
- `brief` keeps core-only navigation and full-scope counts. `context --tier` counts, tags, samples, and omitted are computed from the tier-filtered eligible set. `list --tier` filters before applying its limit.
- Tier audit fields appear only in verbose JSON. Verbose brief remains within the frozen 2048-byte budget and lets `bounded_json` remove rows as needed. Compact output keeps its existing shape and byte count. Capture without `--tier` omits the frontmatter key and keeps status/type inference.
- Legacy `metadata.type` is recognized only when `type` is a direct child. A deeper value such as `metadata.audit.type` does not participate in tier inference.

## Baseline evidence (before implementation)

All commands below ran in this worktree on the host with `HOME=/Users/fanye` where shown.

```text
$ git rev-parse HEAD
c4ff4aa5576df7cdcdb86060f73d3d5e9b1979ca

$ HOME=/Users/fanye md5 -q /Users/fanye/.agent-memory/settings.json
6fd3e8f998ebc9adac011d4a95664d2f

$ HOME=/Users/fanye python3 skills/agent-memory/scripts/agent_memory.py --compact list --project arcp --limit 20 | wc -c
    7216

$ HOME=/Users/fanye python3 skills/agent-memory/scripts/agent_memory.py --compact search 'responsibility loop' --project arcp --limit 10 | wc -c
    1451

$ HOME=/Users/fanye shasum -a 256 skills/agent-memory/scripts/memory_cjk.py eval/agent-memory/retrieval/*
2e955981a368098157b14ff5b9f4f0d290b15a629247b75bdb3959fcab33c653  skills/agent-memory/scripts/memory_cjk.py
798abbf6d0c5dbafefcf4e012200a456e567f7338d2f01bc1ced51a503d2a015  eval/agent-memory/retrieval/benchmark.py
895cddfc1c7ebee6476eb7b94694b2227a5d813887a00c92d0fb07682cbc2613  eval/agent-memory/retrieval/gate.py
f537a045d07cfe610937c8344005bb136d22736e87319d15f386c7f56ac58290  eval/agent-memory/retrieval/judgments.json
9563cb7ed57126c36e1654e816dc4d0e7f192dc3f3681ddae9cf5dac6385ab18  eval/agent-memory/retrieval/measure.py
```

The `pipefail` compact measurement commands exited 0; each CLI produced output consumed by `wc -c`.

## Red-first evidence (before implementation)

Command:

```text
HOME=/Users/fanye python3 -m unittest eval.agent-memory.tests.test_p4_tiers -v
exit code: 1
```

Actual test output:

```text
test_capture_tier_is_explicit_and_omission_keeps_auto_behavior (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_capture_tier_is_explicit_and_omission_keeps_auto_behavior) ... FAIL
test_context_tier_filters_precede_sampling_and_filter_inventory_counts (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_context_tier_filters_precede_sampling_and_filter_inventory_counts) ... FAIL
test_list_tier_filters_compose_and_run_before_limit (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_list_tier_filters_compose_and_run_before_limit) ... FAIL
test_ordinary_json_and_compact_keep_audit_fields_out (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_ordinary_json_and_compact_keep_audit_fields_out) ... FAIL
test_verbose_brief_exposes_tier_and_truthful_precedence (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_verbose_brief_exposes_tier_and_truthful_precedence) ... FAIL

======================================================================
FAIL: test_capture_tier_is_explicit_and_omission_keeps_auto_behavior (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_capture_tier_is_explicit_and_omission_keeps_auto_behavior)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/fanye/.hermes/workspace/am-campaign/p4wt/eval/agent-memory/tests/test_p4_tiers.py", line 231, in test_capture_tier_is_explicit_and_omission_keeps_auto_behavior
    explicit = self.json_cli(
        "capture", "learning", explicit_summary, "--project", "demo",
    ...<2 lines>...
        "--json",
    )
  File "/Users/fanye/.hermes/workspace/am-campaign/p4wt/eval/agent-memory/tests/test_p4_tiers.py", line 105, in json_cli
    self.assertEqual((code, err), (0, ""))
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
AssertionError: Tuples differ: (2, 'usage: agent-memory [-h] [--settings [309 chars]e\n') != (0, '')

First differing element 0:
2
0

+ (0, '')
- (2,
-  'usage: agent-memory [-h] [--settings SETTINGS] [--compact] [--json] '
-  '[--table]\n'
-  '                    [--verbose] [--text] [--yaml] [--envelope]\n'
-  '                    '
-  '{init,status,sync,snapshot,resolve,register,search,list,links,capture,lifecycle,projects,tags,browse,brief,context,doctor} '
-  '...\n'
-  'agent-memory: error: unrecognized arguments: --tier core\n')

======================================================================
FAIL: test_context_tier_filters_precede_sampling_and_filter_inventory_counts (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_context_tier_filters_precede_sampling_and_filter_inventory_counts)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/fanye/.hermes/workspace/am-campaign/p4wt/eval/agent-memory/tests/test_p4_tiers.py", line 184, in test_context_tier_filters_precede_sampling_and_filter_inventory_counts
    core = self.json_cli(
        "--json", "--verbose", "context", "--project", "demo",
        "--tag", "marked", "--tier", "core",
    )
  File "/Users/fanye/.hermes/workspace/am-campaign/p4wt/eval/agent-memory/tests/test_p4_tiers.py", line 105, in json_cli
    self.assertEqual((code, err), (0, ""))
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
AssertionError: Tuples differ: (2, 'usage: agent-memory [-h] [--settings [309 chars]e\n') != (0, '')

First differing element 0:
2
0

+ (0, '')
- (2,
-  'usage: agent-memory [-h] [--settings SETTINGS] [--compact] [--json] '
-  '[--table]\n'
-  '                    [--verbose] [--text] [--yaml] [--envelope]\n'
-  '                    '
-  '{init,status,sync,snapshot,resolve,register,search,list,links,capture,lifecycle,projects,tags,browse,brief,context,doctor} '
-  '...\n'
-  'agent-memory: error: unrecognized arguments: --tier core\n')

======================================================================
FAIL: test_list_tier_filters_compose_and_run_before_limit (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_list_tier_filters_compose_and_run_before_limit)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/fanye/.hermes/workspace/am-campaign/p4wt/eval/agent-memory/tests/test_p4_tiers.py", line 156, in test_list_tier_filters_compose_and_run_before_limit
    eligible = self.json_cli(
        "list", "--json", "--project", "demo", "--tag", "marked",
        "--tier", "core", "--limit", "100",
    )
  File "/Users/fanye/.hermes/workspace/am-campaign/p4wt/eval/agent-memory/tests/test_p4_tiers.py", line 105, in json_cli
    self.assertEqual((code, err), (0, ""))
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
AssertionError: Tuples differ: (2, 'usage: agent-memory [-h] [--settings [309 chars]e\n') != (0, '')

First differing element 0:
2
0

+ (0, '')
- (2,
-  'usage: agent-memory [-h] [--settings SETTINGS] [--compact] [--json] '
-  '[--table]\n'
-  '                    [--verbose] [--text] [--yaml] [--envelope]\n'
-  '                    '
-  '{init,status,sync,snapshot,resolve,register,search,list,links,capture,lifecycle,projects,tags,browse,brief,context,doctor} '
-  '...\n'
-  'agent-memory: error: unrecognized arguments: --tier core\n')

======================================================================
FAIL: test_ordinary_json_and_compact_keep_audit_fields_out (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_ordinary_json_and_compact_keep_audit_fields_out)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/fanye/.hermes/workspace/am-campaign/p4wt/eval/agent-memory/tests/test_p4_tiers.py", line 211, in test_ordinary_json_and_compact_keep_audit_fields_out
    self.assertTrue(
    ~~~~~~~~~~~~~~~^
        all("tier" not in row and "tier_source" not in row for row in ordinary)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
AssertionError: False is not true

======================================================================
FAIL: test_verbose_brief_exposes_tier_and_truthful_precedence (eval.agent-memory.tests.test_p4_tiers.TierCliTests.test_verbose_brief_exposes_tier_and_truthful_precedence)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/fanye/.hermes/workspace/am-campaign/p4wt/eval/agent-memory/tests/test_p4_tiers.py", line 118, in test_verbose_brief_exposes_tier_and_truthful_precedence
    self.assertTrue(
    ~~~~~~~~~~~~~~~^
        all("tier" in row and "tier_source" in row for row in rows)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
AssertionError: False is not true

----------------------------------------------------------------------
Ran 5 tests in 0.685s

FAILED (failures=5)
```

## Green evidence

Focused command after implementation and after the nested-metadata repair:

```text
$ HOME=/Users/fanye python3 -m unittest eval.agent-memory.tests.test_p4_tiers -v
test_capture_tier_is_explicit_and_omission_keeps_auto_behavior ... ok
test_context_tier_filters_precede_sampling_and_filter_inventory_counts ... ok
test_list_tier_filters_compose_and_run_before_limit ... ok
test_ordinary_json_and_compact_keep_audit_fields_out ... ok
test_verbose_brief_exposes_tier_and_truthful_precedence ... ok

----------------------------------------------------------------------
Ran 5 tests in 1.488s

OK
exit code: 0
```

The tests cover explicit > status > type > default conflicts, positive direct
child `metadata.type: feedback`, negative deeper descendant
`metadata.audit.type: feedback`, filtering before list limits and context
sampling, filtered zero-hit scope/count behavior, compact and ordinary-JSON
field invariance, explicit capture, and omitted-tier capture.

## Real-library acceptance

All commands used the repository CLI with `HOME=/Users/fanye` and the real arcp
path `/Users/fanye/.paseo/worktrees/38jp2f3t`.

### Verbose brief audit fields and full counts

Projected machine output:

```json
{
  "status": "ok",
  "scope": {
    "project": "arcp",
    "source": "path",
    "include_global": true,
    "tags": [],
    "resolution_status": "resolved"
  },
  "counts": {
    "distinct": 328,
    "global": 160,
    "project": 168,
    "components_overlap": true,
    "core": 80,
    "archive": 248,
    "defaulted": 123
  },
  "navigation": [
    {"id": 695, "title": "P4 verification marker: explicit core tier 2026-09-21", "tier": "core", "tier_source": "explicit", "scope": "arcp"},
    {"id": 521, "title": "Responsibility-loop entry points must authorize non-mutating outcomes and serialize takeover against the evaluated RunAttempt", "tier": "core", "tier_source": "status", "scope": "arcp"},
    {"id": 496, "title": "ARCP Channel receipt evidence must be fenced to the current RuntimeGeneration", "tier": "core", "tier_source": "status", "scope": "arcp"},
    {"id": 135, "title": "feedback codex lane model luna high", "tier": "core", "tier_source": "type", "scope": "_shared"}
  ],
  "omitted": 324,
  "truncated": true
}
```

The complete command output was 2038 UTF-8 bytes, within the frozen 2048-byte
brief budget. Every retained navigation row still contained both audit fields.

Before the two requested acceptance captures, the initial real-library counts
were `distinct=327 global=161 project=166 core=79 archive=248 defaulted=124`.
The first capture sync removed one stale indexed path whose source no longer
existed. The two intentional captures then produced the final truthful counts
above. No existing memory Markdown was deleted or altered.

### List totals, limits, and composability

The `--limit 1000` commands established the eligible totals; the `--limit 20`
commands were compared with `min(limit, eligible)`:

```json
{
  "core": {
    "exit_codes": [0, 0],
    "eligible": 80,
    "returned": 20,
    "expected_returned": 20,
    "tiers": ["core"],
    "sources": ["explicit", "status", "type"]
  },
  "archive": {
    "exit_codes": [0, 0],
    "eligible": 248,
    "returned": 20,
    "expected_returned": 20,
    "tiers": ["archive"],
    "sources": ["default", "status"]
  }
}
```

The eligible totals equal `brief.counts.core` and `brief.counts.archive`.
Composed project, tag, and tier filtering used tag `p4-test-20260921`:

```text
context --project arcp --tag p4-test-20260921 --tier core
counts: distinct=1 global=0 project=1 core=1 archive=0 defaulted=0
omitted=0; navigation tier=core tier_source=explicit

context --project arcp --tag p4-test-20260921 --tier archive
counts: distinct=1 global=0 project=1 core=0 archive=1 defaulted=0
omitted=0; navigation tier=archive tier_source=status
```

Ordinary `list --json` and `context --json` returned 20 rows each with
`audit_fields_present=false`. Audit fields therefore remain verbose-only.

### Capture validation

Explicit capture command exited 0 and wrote exactly:

```json
{"created":true,"memory_id":"mem_5b35a596242744498ea2b8e3165dbbfb","path":"/Volumes/AgentsWorkspace/orgs/atomlink-ye/tasks/active/arcp/.learnings/learnings/20260921-152428-p4-verification-marker-explicit-core-tier-2026-09-21.md","project":"arcp","kind":"learning","status":"raw","tags":["arcp:learnings","self-improvement:learning","p4-test-20260921"],"tier":"core","sync":{"indexed":687,"removed":1,"missing_roots":0,"roots":4}}
```

```text
---
id: mem_5b35a596242744498ea2b8e3165dbbfb
title: "P4 verification marker: explicit core tier 2026-09-21"
brief: "P4 verification marker: explicit core tier 2026-09-21"
type: learning
status: raw
tier: core
tags: [arcp:learnings, self-improvement:learning, p4-test-20260921]
---

# P4 verification marker: explicit core tier 2026-09-21

**Kind**: learning
**Status**: raw
**Logged**: 2026-09-21T15:24:28.420661+00:00
**Project**: arcp

## Why

Unique P4 acceptance content. This note verifies capture --tier core writes explicit tier frontmatter and appears in the arcp opening brief.
```

Literal source path:

```text
/Volumes/AgentsWorkspace/orgs/atomlink-ye/tasks/active/arcp/.learnings/learnings/20260921-152428-p4-verification-marker-explicit-core-tier-2026-09-21.md
```

Its immediately following verbose brief returned:

```json
{
  "status": "ok",
  "counts": {"distinct": 327, "global": 160, "project": 167, "components_overlap": true, "core": 4, "archive": 323, "defaulted": 199},
  "captured": {"id": 695, "title": "P4 verification marker: explicit core tier 2026-09-21", "tier": "core", "tier_source": "explicit", "scope": "arcp"}
}
```

Those counts exposed the nested `metadata.type` sync defect described above.
After the repair and next sync, the final brief restored the 76 shared feedback
notes to type-derived core while retaining this capture as explicit core.

Omitted-tier capture command exited 0 and wrote exactly:

```json
{"created":true,"memory_id":"mem_8ef2caf717fa432f863b766ea31ec017","path":"/Volumes/AgentsWorkspace/orgs/atomlink-ye/tasks/active/arcp/.learnings/learnings/20260921-152808-p4-verification-marker-omitted-tier-auto-behavior-2026-09-21.md","project":"arcp","kind":"learning","status":"raw","tags":["arcp:learnings","self-improvement:learning","p4-test-20260921"],"possible_duplicates":[{"path":"/Volumes/AgentsWorkspace/orgs/atomlink-ye/tasks/active/arcp/.learnings/learnings/20260921-152428-p4-verification-marker-explicit-core-tier-2026-09-21.md","title":"P4 verification marker: explicit core tier 2026-09-21","score":0.754}],"sync":{"indexed":688,"removed":0,"missing_roots":0,"roots":4}}
```

```text
---
id: mem_8ef2caf717fa432f863b766ea31ec017
title: "P4 verification marker: omitted tier auto behavior 2026-09-21"
brief: "P4 verification marker: omitted tier auto behavior 2026-09-21"
type: learning
status: raw
tags: [arcp:learnings, self-improvement:learning, p4-test-20260921]
---

# P4 verification marker: omitted tier auto behavior 2026-09-21

**Kind**: learning
**Status**: raw
**Logged**: 2026-09-21T15:28:08.392760+00:00
**Project**: arcp

## Why

Unique P4 acceptance content. This note verifies omitted --tier writes no tier key and remains archive through raw status inference.
```

Literal source path:

```text
/Volumes/AgentsWorkspace/orgs/atomlink-ye/tasks/active/arcp/.learnings/learnings/20260921-152808-p4-verification-marker-omitted-tier-auto-behavior-2026-09-21.md
```

`rg '^tier:'` returned no match for that file. The immediately following
verbose archive context returned:

```json
{
  "status": "ok",
  "counts": {"distinct": 248, "global": 83, "project": 165, "components_overlap": true, "core": 0, "archive": 248, "defaulted": 123},
  "omitted": 228,
  "captured": {"id": 696, "title": "P4 verification marker: omitted tier auto behavior 2026-09-21", "tier": "archive", "tier_source": "status", "scope": "arcp"}
}
```

### Compact bytes

The original real-library measurements and the implementation measurements
taken before acceptance captures were identical:

```text
list --project arcp --limit 20: before=7216 after=7216
search 'responsibility loop' --project arcp --limit 10: before=1451 after=1451
```

A second controlled comparison ran base and candidate CLIs against separate
indexes of the same frozen corpus snapshot:

```text
list --project arcp --limit 20: base=4604 candidate=4604
search 'responsibility loop' --project arcp --limit 10: base=1426 candidate=1426
```

### Eight opening queries

Each real-library envelope command exited 0:

```text
memory             results=4 next_commands=1
how do I start     results=1 next_commands=1
conventions        results=0 next_commands=1
what should I know results=4 next_commands=1
setup              results=4 next_commands=1
记忆               results=4 next_commands=1
best practices     results=1 next_commands=1
gotchas            results=0 next_commands=1
actionable=8/8 dead_end=0
```

## Verification and integrity

Full suite:

```text
$ HOME=/Users/fanye python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py' -v
----------------------------------------------------------------------
Ran 99 tests in 4.285s

OK (skipped=1)
exit code: 0
```

The one skip is the existing optional PyYAML round-trip test.

Phase-2 retrieval gate used a frozen snapshot of the real configured arcp and
agent-server roots, with base code archived from
`c4ff4aa5576df7cdcdb86060f73d3d5e9b1979ca` and candidate code from this worktree:

```json
{
  "status": "PASS",
  "phase": 2,
  "query_differences": [],
  "budget": {
    "assessed": true,
    "actual": {
      "storage_bytes": 2449408,
      "sync_median_seconds": 0.07540175,
      "overall_p95_seconds": 0.002772041,
      "ascii_p95_seconds": 0.001281375,
      "han_p95_seconds": 0.003105958
    }
  },
  "failures": []
}
```

Metrics remained exactly:

```text
recall_at_5=0.925
recall_at_10=0.95
mrr_at_10=0.8283333333333334
```

Honest limitation: the gate's pinned canonical corpus path
`/home/agent/am/amcorpus` was absent on this host. The live-root snapshot had
526 indexed documents and SHA256
`c8ab1619db55dc0f703a51d91f18d477fe8d2f6d543fa59dd79c1197116feb75`,
while `measure.py` expects 523 and
`150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c`.
Therefore both base and candidate measurement commands exited 1 on the input
identity check. All 40 judged paths were present, the separate phase-2 gate
command exited 0 with PASS, query differences were empty, and metrics matched
the required values. This is the only acceptance limitation.

Settings checksum:

```text
before: 6fd3e8f998ebc9adac011d4a95664d2f
after:  6fd3e8f998ebc9adac011d4a95664d2f
```

Frozen file SHA256 values equal base:

```text
memory_cjk.py                         2e955981a368098157b14ff5b9f4f0d290b15a629247b75bdb3959fcab33c653
retrieval/benchmark.py                798abbf6d0c5dbafefcf4e012200a456e567f7338d2f01bc1ced51a503d2a015
retrieval/gate.py                     895cddfc1c7ebee6476eb7b94694b2227a5d813887a00c92d0fb07682cbc2613
retrieval/judgments.json              f537a045d07cfe610937c8344005bb136d22736e87319d15f386c7f56ac58290
retrieval/measure.py                  9563cb7ed57126c36e1654e816dc4d0e7f192dc3f3681ddae9cf5dac6385ab18
```

`git diff --check` exited 0. `BRIEF.md` remains untracked and unchanged.

## HANDOFF

Branch: `lane/p4`

HEAD (unchanged; no commit made):
`c4ff4aa5576df7cdcdb86060f73d3d5e9b1979ca`

Changed delivery files:

- `skills/agent-memory/scripts/agent_memory.py`: CLI flags, verbose audit projection, and command plumbing.
- `skills/agent-memory/scripts/memory_navigation.py`: single tier/source precedence, SQL tier expression, filtered inventories.
- `skills/agent-memory/scripts/memory_store_ext.py`: pre-limit list filtering and direct-child-only preservation of legacy nested `metadata.type` during sync.
- `skills/agent-memory/scripts/memory_capture.py`: optional explicit tier validation, frontmatter, and result field.
- `eval/agent-memory/tests/test_p4_tiers.py`: five public-CLI acceptance tests.
- `LANE-P4.md`: this evidence and handoff.

Pre-commit delivery diffstat (temporary index with intent-to-add; `BRIEF.md` excluded):

```text
LANE-P4.md                                       | 523 +++++++++++++++++++++++
eval/agent-memory/tests/test_p4_tiers.py         | 333 +++++++++++++++
skills/agent-memory/scripts/agent_memory.py      |  60 ++-
skills/agent-memory/scripts/memory_capture.py    |   7 +
skills/agent-memory/scripts/memory_navigation.py | 107 +++--
skills/agent-memory/scripts/memory_store_ext.py  |  56 ++-
6 files changed, 1054 insertions(+), 32 deletions(-)
```

No commit, push, PR, merge, settings edit, frozen retrieval edit, or runtime configuration write was performed.
