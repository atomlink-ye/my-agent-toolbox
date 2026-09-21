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

Do not chain `doctor && search` in a script or one-liner: `&&` silently skips search on
both warnings and errors. Warnings are not uniformly harmless: an unreadable/missing root,
stale or unindexed Markdown, a dangling link, or an ambiguous capture root may require
action even when search can still open the index. Handle the code explicitly, and invoke
search separately if it is still intended:

```bash
set +e
"${AM[@]}" --json doctor
doctor_rc=$?
set -e
case "$doctor_rc" in
  0) ;;
  1) printf '%s\n' 'agent-memory doctor reported warnings; review them' >&2 ;;
  2) printf '%s\n' 'agent-memory doctor reported errors; stop writes and diagnose' >&2; exit 2 ;;
  *) printf '%s\n' "unexpected doctor exit: $doctor_rc" >&2; exit "$doctor_rc" ;;
esac
"${AM[@]}" search "<query>" --path /abs/path/to/project
```

## Sync

Run `sync` after manually creating, moving, deleting, or materially editing Markdown
files outside of `capture` (capture already runs `sync` for you). `sync` re-indexes
Markdown under configured roots into the disposable SQLite/FTS5 index; the index is not
authoritative and can always be rebuilt from the Markdown sources.

## Settings contract

Default settings are `~/.agent-memory/settings.json`; the SQLite index defaults beside the
settings file as `index.sqlite3`. Resolution order is an explicit `--settings`, then
`AGENT_MEMORY_SETTINGS`, then `$AGENT_MEMORY_HOME/settings.json`, then the default under
the user's home directory.

`init` creates the settings file only. A fresh registry must run `init` followed by
`sync`; `sync` builds the disposable SQLite index. If settings exist and only the index is
missing, preserve the settings and run `sync` without `init --force`.

Top-level binding paths must be absolute (or `~`-based). Nested `projects[].path` values
may be relative to their parent. Each `memory` entry can be a path or `{path,tags,capture}`.
Child project memory does **not** inherit parent memory unless `inherit_memory: true` is
explicitly set. Binding tags inherit down the tree; root/frontmatter tags are additive.
All three tag sources support the same `parent:child[:leaf]` hierarchy.

See `docs/agent-memory.md` for the complete settings example, data model, tag behavior,
link behavior, capture workflow, and MVE boundaries.

An empty default registry (`shared: []`, `bindings: []`) can return doctor status `ok`
after `sync`, but it has no capture root. Add a binding whose `memory` list contains a
writable root; if it has multiple roots, mark the intended entry with `"capture": true`.
Then run `sync` and use `resolve --path <actual-project>` or `doctor --path
<actual-project>` to verify routing before capture. There is no interactive setup wizard.
When using capture's `--root`, pass the exact resolved path of one configured root.

## Runtime requirements and entrypoint

The runtime needs Python 3.10 or newer, standard-library SQLite with FTS5, and the complete
sibling `scripts/` directory. With `AM_SKILL_DIR` set to the known absolute directory that
contains the loaded `SKILL.md`, this probe exits `0` when the requirements are available:

```bash
set -e
python3 - <<'PY'
import sqlite3
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required")
connection = sqlite3.connect(":memory:")
connection.execute("CREATE VIRTUAL TABLE probe USING fts5(content)")
connection.close()
print("agent-memory requirements: ok")
PY
test -f "$AM_SKILL_DIR/scripts/agent_memory.py"
```

The portable entrypoint is `python3
"$AM_SKILL_DIR/scripts/agent_memory.py"`. A standalone Codex/Agent Skills copy does not
gain an `agent-memory` command merely by being discovered; command lookup and the Python
fallback in `SKILL.md` cover both cases without guessing from CWD.

## Known mechanism caveats

- Read commands use a read-only SQLite connection and do not run schema-creating DDL. On a
  cold read-only filesystem, the runtime may use an immutable view only when it can safely
  rule out a non-empty WAL; it warns on stderr when it does so. A database with WAL state
  that cannot be read safely must fail rather than return a silently stale view. Other I/O
  and corruption errors remain errors and should not be reclassified as harmless.
- For a corrupt index, first determine the configured database path and stop every writer.
  Move the database and any same-name `-wal` and `-shm` sidecars together into a
  recoverable quarantine, then run an explicit `sync` to rebuild from authoritative
  Markdown. Do not delete only the main file, mix sidecars from different moments, or use
  `init --force`; `sync` cannot repair a corrupt database in place.
- Raw search response objects include several fields that are usually null or not
  useful for normal recall decisions: `score` (unnormalized, not comparable across
  queries), `match_mode`/`matched_terms`/`missing_terms` (can mislabel results, e.g.
  marking an OR-matched result as a strict match), and `memory_id`/`type`/`status`/
  `promoted_to`/`superseded_by` (mostly null unless you're deep in the lifecycle
  workflow above). Prefer reading just `title`/`brief`/`path` unless you have a specific
  reason to need the rest.
