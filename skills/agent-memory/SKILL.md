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

## Daily consumption loop: brief, read, then narrow

At the start of a task, run one **unparameterized** `brief`. Do this before inventing a
query such as `conventions`, `setup`, or `best practices`:

```bash
"${AM[@]}" --json brief
```

The brief is the map of the current visible scope. Check its resolved scope, document
count, index state, tags, topic hooks, and navigation. For each hook that is relevant to
the task, use the agent's ordinary file-reading tool to read the routed Markdown topic.
The CLI output is routing metadata, not the memory content and not a substitute for
reading the source file.

If the brief does not expose enough routes, expand the same visible scope:

```bash
"${AM[@]}" --json context
```

Use a targeted search only when the task supplies a concrete term, identifier, error, or
other evidence worth locating:

```bash
"${AM[@]}" --json search "<specific evidence>" --path /abs/path/to/project
```

`search`, `context`, and `brief` route to authoritative Markdown. Read the returned topic
paths with the ordinary Read tool before applying their guidance. For search/list compact
output, join the printed memory-root base and relative path.

One zero match means only that **this query** did not match. It is not evidence that the
visible scope contains no memory. Inspect the returned navigation, document count, and
tags, then execute the single most relevant next command. Do not summarize `no_match` as
"there is no memory."

After the brief, expanded index/context, and one evidence-based alternative query have
all failed to route to a relevant topic, state: "No relevant memory was found in the
currently visible scope." Then continue the user's task without memory; do not keep
searching or manufacture more synonyms.

Run another unparameterized `brief` when the active project changes, or after context
compression when the earlier index/scope is no longer visible. Do not repeat it on every
turn while the same map remains available.

### Routing output details

- `search` and `list` default to the compact routing format. Each result contains a title,
  an optional non-duplicate brief, and a path relative to the printed memory-root base.
  Join the base and relative path, then **read that Markdown file**. Search output is a
  route to the source of truth, not the source content itself.
- A suffix such as `~relaxed`, `~cjk`, or `~hybrid` identifies a non-strict retrieval
  route. Strict matches have no suffix. Use `--compact` to request this format explicitly.
- Use `--json` only for scripts that need structured rows. It emits whitespace-minified
  JSON, omits null/empty fields, and preserves existing non-empty field names. For search
  diagnostics, use `--json --verbose` (or bare `--verbose`) to get the complete,
  pretty-printed record including match mode, scores, and matched/missing terms.
- `--path` is worth passing but not worth over-engineering here: an unbound or wrong
  path can broaden a read to global scope. Confirm the reported scope before using a
  route. A zero match follows the bounded recovery rule above, not an unbounded synonym
  loop.
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
