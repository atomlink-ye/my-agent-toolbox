# Doctor internals, sync, and settings contract

## Doctor

Use `doctor` when setup may be stale, a link looks broken, capture routing is unclear, or
an Agent has inherited an unfamiliar machine/workspace. It is read-only.

`doctor` checks:

- project binding duplication/ambiguity and reused project names;
- missing/unreadable memory roots and writable capture/database locations;
- ambiguous or duplicate `capture: true` roots;
- SQLite `PRAGMA quick_check`;
- indexed files that disappeared or changed since the last sync;
- Markdown under configured roots that is not indexed yet;
- dangling local Markdown links (including targets deleted after sync) versus existing local
  files outside the memory graph;
- deterministic tag case/separator collisions;
- physical memory roots reused by multiple project scopes.

`doctor --path <actual-worktree>` also reports the resolved project, binding, memory roots,
and default capture root. Exit codes are `0=ok`, `1=warnings`, `2=errors`.

Do not chain `doctor && search` in a script or one-liner: `doctor` frequently exits
non-zero (warnings=1 or errors=2) even when the index and search path are perfectly
usable, and `&&` will silently skip the `search` entirely. Run them as separate
statements (or with `;`) if you want both to execute regardless of doctor's exit code.

## Sync

Run `sync` after manually creating, moving, deleting, or materially editing Markdown
files outside of `capture` (capture already runs `sync` for you). `sync` re-indexes
Markdown under configured roots into the disposable SQLite/FTS5 index; the index is not
authoritative and can always be rebuilt from the Markdown sources.

## Settings contract

Default settings are `~/.agent-memory/settings.json`; the SQLite index defaults beside the
settings file as `index.sqlite3`. Override the settings file with `--settings` or
`AGENT_MEMORY_SETTINGS`.

Top-level binding paths must be absolute (or `~`-based). Nested `projects[].path` values
may be relative to their parent. Each `memory` entry can be a path or `{path,tags,capture}`.
Child project memory does **not** inherit parent memory unless `inherit_memory: true` is
explicitly set. Binding tags inherit down the tree; root/frontmatter tags are additive.
All three tag sources support the same `parent:child[:leaf]` hierarchy.

See `docs/agent-memory.md` for the complete settings example, data model, tag behavior,
link behavior, capture workflow, and MVE boundaries.

## Known mechanism caveats (see audit for full detail)

- `search`/`list`/`doctor` etc. currently open the SQLite connection for write and run
  DDL even for read-only operations, which can fail with `unable to open database file`
  on a read-only filesystem. If you hit that error on an otherwise-correct command, it is
  this known issue, not a sign the index is corrupt.
- Raw search response objects include several fields that are usually null or not
  useful for normal recall decisions: `score` (unnormalized, not comparable across
  queries), `match_mode`/`matched_terms`/`missing_terms` (can mislabel results, e.g.
  marking an OR-matched result as a strict match), and `memory_id`/`type`/`status`/
  `promoted_to`/`superseded_by` (mostly null unless you're deep in the lifecycle
  workflow above). Prefer reading just `title`/`brief`/`path` unless you have a specific
  reason to need the rest.
