# P2 — local and global memory balance

## Design decisions

- Read-time scope resolution now goes through `memory_identity.resolve_context()`; write routing keeps its stricter existing path resolver.
- A live repository may recover a deleted linked-worktree binding only from its own prunable `git worktree --porcelain` record. Remote URLs, commit IDs, names, and HEADs are not identity, so independent clones remain separate.
- A non-Git task can resolve through an exact, uniquely owned `.learnings` memory root. An unowned root stays unregistered and gets an explicit `agent-memory register` command. Reads never edit settings; the explicit command uses a sibling lock and atomic replacement.
- `brief` context terms come only from cwd/repository names, branch, and changed filenames. They rank navigation metadata (title, indexed brief, tags, path) but never change search scope, match truth, or retrieval scoring.
- Registered navigation is current project first, then `_shared`. Unregistered navigation expands `_shared` only and represents every project as `{project, count}`.

## Red evidence

Command:

```text
python3 -m unittest \
  eval.agent-memory.tests.test_arch_identity.IdentityResolverTests.test_missing_linked_worktree_binding_is_recovered_from_repo_identity \
  eval.agent-memory.tests.test_arch_identity.IdentityResolverTests.test_unregistered_repo_with_known_memory_root_proposes_explicit_registration \
  eval.agent-memory.tests.test_arch_brief.BriefContractTests.test_scope_priority_and_context_change_brief_selection
```

Observed before implementation (`rc=1`):

```text
FAILED (failures=1, errors=2)
AssertionError: 'unregistered' != 'resolved'
StopIteration: no registration_suggestion diagnostic
AttributeError: agent_memory ... does not have the attribute 'current_context_terms'
```

## Green evidence

Full suite:

```text
python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py'
Ran 85 tests in 2.555s
OK (skipped=1)
```

The skip is the pre-existing optional PyYAML check. The fixed eight opening queries remain actionable with `dead_end=0`:

```text
python3 -m unittest eval.agent-memory.tests.test_arch_integration.AgentMemoryArchitectureIntegrationTests.test_opening_query_acceptance_and_synthetic_all_miss_fixture
Ran 1 test in 0.464s
OK
```

Pinned-corpus retrieval comparison used baseline code from `HEAD` and candidate code from this worktree against the same 523 documents:

```text
before_rc=0 after_rc=0 gate_rc=0
before_valid=true after_valid=true
before metrics: recall@5=0.925 recall@10=0.95 mrr@10=0.8283333333333334
after  metrics: recall@5=0.925 recall@10=0.95 mrr@10=0.8283333333333334
phase 2: PASS
failures=[]
```

The corpus SHA was the pinned `150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c`; the gate assessed storage, sync, and latency budgets.

Real read-only settings (`~/.agent-memory/settings.json`):

```text
$ python3 skills/agent-memory/scripts/agent_memory.py --settings ~/.agent-memory/settings.json brief --path /Volumes/AgentsWorkspace/orgs/atomlink-ye/tasks/active/arcp
status=ok project=arcp source=path
counts: distinct=327 global=161 project=166 (components may overlap)
diagnostic: resolved project 'arcp' from its unique configured .learnings root
navigation:
- [arcp] Mutation audit found exactly one weak assertion in five consecutive sweeps
...
- [_shared] ARCP and Agent Server are separate current projects
...
next: agent-memory context --project arcp
```

Context-dependent global selection uses actual metadata relevance, not hashing:

```text
/Users/fanye/.hermes -> [(135, 'feedback codex lane model luna high'),
                         (440, 'ARCP and Agent Server are separate current projects')]
/Users/fanye/.codex  -> [(135, 'feedback codex lane model luna high'),
                         (303, '当前 Agent 工作流与模型路由（2026-08-23）')]
```

Both are `status=unregistered`, expose `_shared` briefs, and summarize other scopes only as:

```text
[{'project': 'agent-server', 'count': 360}, {'project': 'arcp', 'count': 166}]
```

UTF-8 byte counts from real text output:

```text
arcp:    2024
.hermes: 1246
.codex:  1249
```

`git diff --check` passed. `memory_cjk.py` and all four files under `eval/agent-memory/retrieval/` have zero diff.

## HANDOFF

- Stale linked-worktree recovery intentionally fails closed after Git prunes the worktree record; v1 settings contain no safe identity evidence after that point.
- The real index currently reports `agent-server=360`, which includes its configured authority root; this P2 does not migrate, rewrite, or normalize existing memory files or index layout.
- Broader registration lifecycle commands (`remove`, rename, nested binding editing) remain outside P2. Only the explicit `.learnings` add path required by the suggestion is implemented.
