---
name: agent-memory
description: "TRIGGER — before non-trivial work or ‘have we seen this?’ questions, recall; before reporting a durable event, capture user corrections (‘no’, ‘actually’, ‘其实’), reusable bug fixes, drawbacks, proven better practices, or feature requests. Use doctor when memory routing/index/link health is uncertain. SKIP only typos/transient failures, secrets, raw transcripts, or expiring trivia."
---

# Agent Memory

Local, file-first memory for agents that may start from one directory while working on
many different projects. Markdown files are authoritative; SQLite is a disposable
search index. Setup once: `"${CLAUDE_PLUGIN_ROOT}/skills/agent-memory/scripts/setup.sh"`.

## Recall before starting non-trivial work

```sh
agent-memory --json search "<query>" --path "$PWD"
```

- Run this before non-trivial work, or whenever asking "have we seen this before?"
- Prefer the returned `brief` to judge relevance, then read the returned `path` — the
  Markdown file, not the JSON row, is the source of truth.
- **Chinese queries**: the tokenizer turns a whole run of contiguous Chinese characters
  into one opaque token, so a full Chinese sentence often gets zero hits even when the
  answer exists. Space-segment it yourself (`"委托 子代理 结束 回合 死亡"`, not
  `"委托子代理后结束回合会导致子代理死亡"`), or mix in an English anchor term.
- `--path` is worth passing but not worth over-engineering here: an unbound or wrong
  path silently falls back to a global search rather than failing. If a query genuinely
  returns nothing, retry with fewer/looser terms before concluding it doesn't exist.
- Never chain `doctor && search` — `doctor` often exits non-zero on harmless warnings,
  which short-circuits `&&` and skips `search` entirely. Run them as separate commands.

## Capture durable knowledge

```sh
agent-memory --json capture <kind> "<one-line summary>" --path /abs/path/to/project \
  --details "..." --action "..." --tag <project>:<category>
```

Kinds: `learning`, `drawback`, `error`, `feature-request`.

Unlike search, **`--path`/`--project` correctness matters a lot here**: capture writes
into that project's memory root, and a wrong or unbound path misfiles the memory or makes
capture refuse to guess. Use the real project/worktree path, not the process CWD; run
`agent-memory projects` first if unsure which project a path resolves to.

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
