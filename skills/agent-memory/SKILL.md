---
name: agent-memory
description: "TRIGGER — before non-trivial work or ‘have we seen this?’ questions, recall; before reporting a durable event, capture user corrections (‘no’, ‘actually’, ‘其实’), reusable bug fixes, drawbacks, proven better practices, or feature requests. Use doctor when memory routing/index/link health is uncertain. SKIP only typos/transient failures, secrets, raw transcripts, or expiring trivia."
---

# Agent Memory

Local, file-first memory for agents that may start from one directory while working on
many different projects. Markdown files are authoritative; SQLite is a disposable
search index.

## Preflight: resolve the runtime from this skill

Requires Python 3.10 or newer, its standard-library `sqlite3` module with FTS5 enabled,
and this skill's **complete** `scripts/` directory. Do not copy only
`agent_memory.py`, and do not infer the skill location from the process CWD.

Set `AM_SKILL_DIR` to the absolute directory containing the loaded `SKILL.md`. Codex,
standalone Agent Skills consumers, and bare CLI users must obtain that path from their
skill installation/discovery mechanism. Then select the installed command when present,
or the skill-local Python entrypoint otherwise:

```bash
AM_SKILL_DIR=/absolute/path/to/loaded/agent-memory
if ! test -f "$AM_SKILL_DIR/SKILL.md" || \
   ! test -f "$AM_SKILL_DIR/scripts/agent_memory.py"; then
  printf '%s\n' "invalid agent-memory skill directory: $AM_SKILL_DIR" >&2
  exit 2
fi
if command -v agent-memory >/dev/null 2>&1; then
  AM=(agent-memory)
else
  AM=(python3 "$AM_SKILL_DIR/scripts/agent_memory.py")
fi
```

The fallback installs no bare command; keep the entire skill directory in place. Before
first use, verify Python/FTS5 as described in
`references/doctor-sync-settings.md`, then classify the registry with:

```bash
"${AM[@]}" --json status
```

- Exit `0`: ready.
- Exit `2` with `settings not found`: run `init`, then `sync`, in that order.
- Exit `2` with `index not initialized`: preserve the settings and run only `sync`.
- Any other exit `2`: stop and diagnose the reported settings, database, permission, or
  corruption error. Do not use `init --force` as automatic recovery.

The complete first bootstrap is therefore:

```bash
"${AM[@]}" --json init
"${AM[@]}" --json sync
"${AM[@]}" --json status
```

Each command should exit `0`. `init` alone creates settings, not the SQLite index.

For a Claude Code plugin only, plugin content may provide this convenience substitution:

```bash
AM_SKILL_DIR="${CLAUDE_PLUGIN_ROOT}/skills/agent-memory"
```

That substitution has not been end-to-end verified in this repository's test environment.
It is not a normal Bash environment variable and must not be used by Codex, a standalone
skill copy, or a bare terminal.

## Recall before starting non-trivial work

```bash
"${AM[@]}" --json search "<query>" --path /abs/path/to/project
```

- Run this before non-trivial work, or whenever asking "have we seen this before?"
- Prefer the returned `brief` to judge relevance, then read the returned `path` — the
  Markdown file, not the JSON row, is the source of truth.
- **Current Chinese-search limitation**: the current `unicode61` index can store a whole
  contiguous Han run as one opaque token. Adding spaces to the query does not split the
  already-indexed text, so it is not a general workaround. Until the auxiliary Han index
  is present and an explicit `sync` has populated it, use a known ASCII anchor when one
  exists and treat zero results as inconclusive. The planned Han route is substring
  retrieval, not general Chinese word segmentation or semantic search.
- `--path` is worth passing but not worth over-engineering here: an unbound or wrong
  path silently falls back to a global search rather than failing. If a query genuinely
  returns nothing, retry with fewer/looser terms before concluding it doesn't exist.
- Never chain `doctor && search`: `doctor` uses `0=ok`, `1=warnings`, and `2=errors`, so
  `&&` skips search for both warning and error. Warnings can be actionable (for example,
  stale/unindexed Markdown or a missing root); inspect them, but run an intended search as
  a separate command and handle the doctor code explicitly.

## Capture durable knowledge

```bash
"${AM[@]}" --json capture <kind> "<one-line summary>" --path /abs/path/to/project \
  --details "..." --action "..." --tag <project>:<category>
```

Kinds: `learning`, `drawback`, `error`, `feature-request`.

Unlike search, **`--path`/`--project` correctness matters a lot here**: capture writes
into that project's memory root, and a wrong or unbound path misfiles the memory or makes
capture refuse to guess. Use the real project/worktree path, not the process CWD; run
`"${AM[@]}" --json resolve --path /abs/path/to/project` first if unsure which project a
path resolves to. If using `--root`, its resolved path must exactly match a configured
memory root. A newly initialized empty registry can pass `doctor` but still has no
capture destination; add a binding and memory root (mark one root `"capture": true` when
there are several), then run `sync` before capturing.

## Deciding whether an event is worth capturing

Capture one concise memory only when it will change how *future* work should be done:

- the user corrects an incorrect assumption or result → `learning`
- a better recurring approach is discovered → `learning`
- an architectural/tool/workflow drawback becomes clear → `drawback`
- a command/integration/API/runtime fails in a reusable way → `error`
- the user requests a missing reusable capability → `feature-request`

Skip: routine transient failures, secrets, raw transcripts, noisy debugging output, or
anything that will obviously be stale immediately. When unsure, skip it — a low-value
capture is rarely re-read, while a missing one just means the lesson gets rediscovered.

## Everything else

Lifecycle states, snapshots/backups, links/backlinks, `browse`/`tags` administration,
`doctor` internals, `sync`, and the settings schema are lower-frequency; see
`references/lifecycle-snapshot-links.md`, `references/browse-and-tags.md`,
`references/doctor-sync-settings.md`, and `docs/agent-memory.md`.
