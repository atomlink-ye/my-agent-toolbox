# Local Agent Memory Registry

## Problem

Coding agents are often launched from a workspace, task bundle, or orchestration root
that is not the repository they are currently working on. A single launch directory may
control several unrelated projects. CWD-scoped memory (`CLAUDE.md`, `.memory/`, or a
project-local capture workflow) therefore creates two failure modes:

1. **missed memory** — useful durable knowledge lives in another project or domain folder;
2. **cross-project leakage** — an agent launched above projects A and B accidentally uses
   A's memory while working on B.

The desired system is not a conversation-memory engine. It is a small local registry that
answers: *which durable files are relevant, where are they, and how are they related?*

## MVE goals

- Markdown files remain the source of truth and stay directly editable by humans/agents.
- One local SQLite database indexes paths, titles, briefs, tags, scopes, full text, and
  link relationships.
- Project identity is resolved from an explicit local settings file, not inferred from the
  agent process CWD.
- Nested project mappings support a workspace that contains many projects with different
  memory roots.
- The most-specific (longest path prefix) mapping wins.
- Child projects do not inherit parent memory unless `inherit_memory: true` is explicit.
- Shared/domain memory can be visible to every project without merging project-specific
  scopes.
- Tags can be hierarchical (`parent:child[:leaf]`) while remaining searchable by any
  complete subtag/segment such as `child`; results preserve the full canonical tag.
- Standard Markdown links create a local graph; outbound links and inbound backlinks can
  cross project boundaries.
- The CLI returns compact briefs and absolute file paths first; agents open the actual file
  only when needed.
- Search uses SQLite FTS5/BM25. No embedding service, vector DB, daemon, cloud account, or
  LLM extraction is required.

## Non-goals for this MVE

- Automatically learning from every chat/session.
- Replacing Claude Code/Codex/OpenCode session memory.
- Semantic/vector search.
- Background filesystem watchers.
- Conflict resolution or multi-device sync.
- A graph database or transitive graph reasoning.
- Automatic consolidation, decay, or scoring policies.
- MCP as the primary transport. The CLI is the stable core; MCP can be an adapter later.

## Local layout

```text
~/.agent-memory/
  settings.json        # authoritative path/scope configuration
  index.sqlite3        # disposable derived index

~/memory/
  shared/
    workflows/
      review.md
  agent-server/
    architecture.md
    ui-decisions.md
  other-project/
    decisions.md
```

The Markdown files do not need to live under `~/.agent-memory/`; they may live anywhere
on the local filesystem. The settings file only records where to find them.

## Runtime and first bootstrap

Agent Memory requires Python 3.10 or newer, the standard-library `sqlite3` module with
FTS5 enabled, and the complete `skills/agent-memory/scripts/` directory. It has no
third-party Python package, daemon, network, Node, or pnpm runtime dependency. A repository
global link may use pnpm, but a standalone skill copy runs directly through Python.

Start from the absolute directory containing the `SKILL.md` that the current runtime
actually loaded. Do not derive it from the calling CWD:

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

Use `"${AM[@]}"` in place of a bare `agent-memory` in the examples below. A Codex
standalone copy supplies its installed skill directory; it does not automatically put a
CLI on PATH. In Claude Code plugin content only,
`AM_SKILL_DIR="${CLAUDE_PLUGIN_ROOT}/skills/agent-memory"` is the documented convenience
substitution. That Claude integration has not been end-to-end verified in this
repository's test environment; `${CLAUDE_PLUGIN_ROOT}` is not a normal Bash variable.

Check `"${AM[@]}" --json status` first. A fresh bootstrap is two distinct mutations:

```bash
"${AM[@]}" --json init
"${AM[@]}" --json sync
"${AM[@]}" --json status
```

All three exit `0` on success. `init` creates only settings; `sync` creates/rebuilds the
derived index. If status reports only `index not initialized`, keep the existing settings
and run only `sync`. For malformed settings, corruption, or other I/O errors, stop and
diagnose instead of using `init --force`.

## Settings

Default path: `~/.agent-memory/settings.json`.

An explicit global `--settings` option has highest priority, followed by
`AGENT_MEMORY_SETTINGS`, then `$AGENT_MEMORY_HOME/settings.json`, then the default above.

```json
{
  "version": 1,
  "database": "~/.agent-memory/index.sqlite3",
  "shared": [
    {
      "path": "~/memory/shared",
      "tags": ["shared:domain"]
    }
  ],
  "bindings": [
    {
      "path": "~/workspace",
      "project": "workspace",
      "memory": ["~/memory/workspace"],
      "tags": ["workspace"],
      "projects": [
        {
          "path": "agent-server",
          "project": "agent-server",
          "memory": [
            {
              "path": "~/memory/agent-server",
              "tags": ["agent-server:knowledge", "knowledge:decisions"]
            }
          ],
          "tags": ["agent-server"]
        },
        {
          "path": "experiments/ui-a",
          "project": "ui-a",
          "memory": ["~/memory/ui-a"],
          "tags": ["prototype:ui"]
        },
        {
          "path": "experiments/ui-b",
          "project": "ui-b",
          "memory": ["~/memory/ui-b"],
          "tags": ["prototype:ui"]
        }
      ]
    }
  ]
}
```

### Path resolution rules

- A top-level `bindings[].path` must be absolute after `~` / environment expansion. This
  keeps configuration independent from the process CWD.
- A nested `projects[].path` may be relative to its parent binding.
- A relative `memory` entry is resolved against that binding's resolved project path.
- A `memory` entry may be a path string or `{ "path": "...", "tags": [...] }`; root tags are merged with binding and Markdown tags.
- When several bindings contain the target path, the longest/more-specific path wins.
- Two equally specific bindings are an error instead of an arbitrary choice.
- If a target is an ancestor of exactly one binding, it resolves to that binding. If it is
  an ancestor of multiple bindings, strict resolution refuses to guess and lists the
  candidates with `projects` / `browse` / `--project` guidance.
- `memory` roots from the parent are **not** inherited by a child by default. Set
  `"inherit_memory": true` on the child to opt in.
- Binding tags are inherited by children. Tags classify documents; they do not grant
  project visibility.
- `shared` roots are indexed under the reserved `_shared` scope. A project-scoped query
  includes `_shared` unless `--no-shared` is requested.

For the read-only `search`, `list`, `tags`, and `browse` commands, `--project` and `--path`
are mutually exclusive. An unbound `--path`, or one above multiple descendant bindings,
falls back to global scope; invalid settings and equally-specific containing bindings still
fail. `resolve`, `capture`, and `doctor --path` remain strict and fail rather than guessing
a project or capture root. `capture --project <name>` is an explicit alternative to
`--path`, only when that name identifies exactly one binding; duplicate names are rejected.

This asymmetry is important: a mistyped/unbound search `--path` can silently broaden the
query to global scope, while capture is strict. A wrong but valid path can therefore expose
unintended search results; a wrong capture target can select the wrong binding and misfile
the new Markdown. Verify capture routing with `resolve --path /actual/project/path` or
`doctor --path /actual/project/path` rather than substituting the agent's launch CWD.

This makes the common multi-project workspace safe by default:

```text
~/workspace/project-a/...  -> project-a memory + shared memory
~/workspace/project-b/...  -> project-b memory + shared memory
```

Project A and B do not see each other's project memory simply because the agent was
launched from `~/workspace`.

## Hierarchical tags

Tags from bindings, memory roots, and Markdown frontmatter all support a colon-delimited
hierarchy:

```text
agent-server:operations
agent-server:architecture
agent-server:operations:deploy
workflow:review
```

The stored value is always the full canonical tag. Whitespace around hierarchy separators
is normalized, so `agent-server : operations` becomes `agent-server:operations` during
indexing/config resolution. Empty hierarchy segments such as `agent-server::operations`
are rejected.

Tag filtering works on complete colon-delimited segments/subpaths, case-insensitively:

```text
stored tag: agent-server:operations:deploy

--tag operations                 -> match
--tag deploy                     -> match
--tag agent-server:operations    -> match
--tag OPERATIONS                 -> match
--tag ops                        -> no match
```

The important display rule is that a subtag is a **lookup alias, not a second stored tag**.
A query by `--tag operations` still returns:

```json
{
  "tags": ["agent-server:operations:deploy"]
}
```

It does not return or persist an extra flat `operations` tag. This preserves the context
that the operations knowledge belongs under `agent-server`.

When several `--tag` arguments are supplied, they are ANDed. For example,
`--tag operations --tag runbook` requires a document to have a canonical tag containing
`operations` and another (or the same hierarchical tag) containing `runbook` as complete
segments.

Hierarchical tags are classification only. They do not change project scope, shared
visibility, or the longest-prefix binding rules.

## Markdown memory format

Any `.md` file under a configured memory root is indexed. Frontmatter is optional. The MVE
supports `title`, `brief`, `type`, and `tags` in a deliberately small YAML-like subset:

```md
---
title: Agent Team Operations
brief: Durable operating knowledge for Agent Teams deployment and maintenance.
type: reference
tags: [agent-server:operations, knowledge:runbook]
---

# Agent Team Operations

...
```

If `title` is absent, the first H1 (or filename) is used. If `brief` is absent, the first
non-heading paragraph is used. Tags from settings and frontmatter are merged and kept in
canonical hierarchical form.

`type` is an explicit source-level classification (for example `learning`, `error`,
`feedback`, or `reference`). It remains deliberately separate from project scope and tags;
the MVE preserves it in Markdown rather than adding another filter surface prematurely.

Project scope is controlled by settings rather than file frontmatter. A note cannot place
itself into another project's search scope by declaring a metadata field.

## References and backlinks

The index extracts ordinary local Markdown links:

```md
See the [shared review workflow](../../shared/workflows/review.md).
```

It also accepts simple relative wikilinks:

```md
See [[architecture-decisions]].
```

HTTP(S), mail, and other external schemes are not added to the local graph. Local links
are normalized to absolute paths in the derived index while the Markdown source remains
unchanged.

After `sync`, every edge records:

```text
source document
  -> original href / label / optional anchor
  -> normalized target path
  -> resolved target document id (when indexed)
```

`agent-memory links <document>` returns both:

- `outbound`: documents referenced by the source, including dangling local targets;
- `inbound`: indexed documents that reference the source (backlinks).

Because identity is the normalized source file path, a project-specific note can link to a
shared domain note, or one project can explicitly reference another project's durable
knowledge, without globally merging their search scopes.

## Stable IDs and lifecycle

Captured memories receive an opaque frontmatter `id: mem_<uuid>` and default to
`status: raw`; the capture result also returns that ID as `memory_id`. IDs are logical
identity while the Markdown path remains the physical source location, so a stable
cross-project reference can survive a file move:

```md
[Canonical workflow](memory://mem_012345...)
```

`agent-memory links mem_012345...` resolves these references alongside ordinary Markdown
links. Existing Markdown without an ID remains valid. Doctor reports it as migration info;
use `skills/agent-memory/scripts/migrate_ids.py` only when stable references are needed.

Lifecycle records a reuse decision, not routine editing. The valid statuses are:

- `raw` — captured evidence not yet independently confirmed;
- `validated` — guidance confirmed through another check or use;
- `promoted` — retained evidence that points to a canonical reusable memory;
- `superseded` — retained provenance replaced by a newer canonical memory.

The common progression is `raw` → `validated`; once repeated evidence is consolidated, use
the canonical memory ID as the target for promoted evidence and superseded duplicates:

```sh
agent-memory lifecycle mem_evidence validated
agent-memory lifecycle mem_evidence promoted --target mem_canonical
agent-memory lifecycle mem_old superseded --target memory://mem_canonical
```

`promoted` writes `promoted_to: memory://…`; `superseded` writes
`superseded_by: memory://…`. Both require `--target`; each lifecycle command re-indexes the
registry. Doctor checks ID validity/duplication, lifecycle values, and missing lifecycle or
`memory://` targets.

## SQLite model

SQLite is rebuildable derived state:

```text
documents
  id, path, title, brief, mtime_ns, size, sha256

document_scopes
  document_id, scope

document_tags
  document_id, tag          # full canonical hierarchy only

document_fts (FTS5)
  rowid -> title, brief, content

links
  source_document_id
  target_path
  target_document_id?   # null when dangling/not indexed
  href, label, anchor
```

Subtag lookup does not need duplicate rows or a second alias table in the MVE. Filtering
matches complete `:`-delimited segments against the canonical value in `document_tags`.

The same physical document can have multiple scopes when a memory root is intentionally
reused or inherited. This is represented in `document_scopes` rather than duplicating the
file.

## CLI

The implementation is standard-library-only Python 3.10+ plus SQLite/FTS5.

`search` and `list` emit compact routing hints by default; each relative result path is
resolved against the absolute base printed above it. `browse` defaults to grouped text,
and other subcommands default to deterministic YAML. Choose one mutually exclusive output
flag when needed: `--compact` explicitly selects routing hints, `--json` emits minified
machine-readable JSON with null/empty fields omitted, `--table` renders compact tables,
`--text` selects the legacy human-readable output, and `--yaml` explicitly selects YAML.
Add `--verbose` to `--json` (or use it alone) for complete pretty-printed diagnostics.
For example:

```sh
agent-memory status                 # YAML (default)
agent-memory --json status          # JSON for scripts
agent-memory search "sandbox filesystem" --path ~/workspace/agent-server # compact route; then read the path
agent-memory --json --verbose search "sandbox filesystem" # full search diagnostics
agent-memory --table projects       # compact table
agent-memory --text doctor          # legacy human-readable view
agent-memory --yaml browse          # explicit YAML instead of browse's text default
agent-memory browse                 # [shared] / [project: <name>] groups with title, brief, and path
agent-memory --json browse --project agent-server
```

```sh
# bootstrap (init alone is not ready)
agent-memory --json init
agent-memory --json sync

# verify paths / counts
agent-memory --json status
agent-memory --json resolve --path ~/workspace/agent-server

# rebuild the derived registry after source changes
agent-memory --json sync

# project-safe recall; shared memory is included
agent-memory search "sandbox filesystem" --path ~/workspace/agent-server
agent-memory search "deployment" --project agent-server --tag operations
agent-memory search "deployment" --project agent-server --tag agent-server:operations
agent-memory list --path ~/workspace/agent-server --tag architecture

# deliberately global/cross-project recall
agent-memory search "review workflow"
agent-memory browse --path ~/workspace/agent-server --tag operations

# graph inspection
agent-memory --json links ~/memory/agent-server/ui-decisions.md

# one source-read-only archive; default: <settings-dir>/snapshots/
agent-memory --json snapshot
agent-memory --json snapshot --output ~/backups/agent-memory
agent-memory --json snapshot search ~/backups/agent-memory/agent-memory-20260820T120000Z.tar.gz "sandbox ownership"
agent-memory --json snapshot inspect ~/backups/agent-memory/agent-memory-20260820T120000Z.tar.gz
```

The global `--settings PATH` option supports alternate registries and tests. The
`AGENT_MEMORY_SETTINGS` and `AGENT_MEMORY_HOME` variables can set the defaults as described
above.

After a default `init` and `sync`, status and doctor can be healthy while the registry is
empty. Its settings contain no binding or memory root, so capture has nowhere to write.
Add a binding and at least one writable `memory` root using the Settings schema above;
mark one root with `"capture": true` when several roots could receive captures. Run
`sync`, then verify the actual worktree with `resolve --path` or `doctor --path`. Agent
Memory intentionally provides no interactive setup wizard. A capture `--root` must exactly
match one configured root after path expansion/resolution.

## Search language boundary

The current word index uses SQLite FTS5 `unicode61`, which can index a contiguous Han run
as one token. Splitting a Chinese query with spaces does not split the text already in the
index and is not a general workaround. Until a runtime with the auxiliary Han substring
route is installed and an explicit `sync` has populated its derived table, use a known
ASCII anchor when available and treat zero results as inconclusive. The auxiliary route is
intended for Han substring recall; it is not Chinese word segmentation, paraphrase, or
semantic search.

## Doctor exit codes

Doctor is read-only and returns `0` for ok, `1` for warnings, and `2` for errors. Do not
write `doctor && search`: both warnings and errors short-circuit the search. Warnings may
identify stale/unindexed Markdown, missing roots, dangling links, or ambiguous capture
routing, so inspect them rather than treating every warning as harmless. If a search is
still intended, run it as a separate command after explicitly handling the doctor code.

Read commands open SQLite read-only and do not run schema-creating DDL. A safe immutable
fallback may be used for a cold read-only database only when no non-empty WAL can be lost;
the CLI reports that fallback on stderr. Unsafe WAL, other I/O failures, and corruption
remain errors.

For corruption recovery, locate the configured database, stop all writers, and move the
main database plus any matching `-wal` and `-shm` sidecars together to a recoverable
quarantine. Then run an explicit `sync` to rebuild the disposable index from Markdown.
Never delete only one member of the SQLite file set, combine files from different points
in time, or overwrite settings with `init --force`; `sync` is a rebuild after quarantine,
not an in-place database repair.

## Snapshots

`agent-memory snapshot` creates one timestamped `.tar.gz` archive without modifying
Markdown sources, `settings.json`, or the index. It does not run `sync` and has no
background scheduler; cron or launchd can invoke the command periodically when desired.

The default output directory is `snapshots/` beside the active settings file. Override it
per invocation with `--output DIRECTORY`. Repeated invocations create distinct timestamped
archives (with a numeric suffix for a same-second collision) rather than overwriting an
existing recovery point.

Each archive contains:

```text
settings.json
database/index.sqlite3             # consistent SQLite backup
database/sidecars/index.sqlite3-*  # original WAL/SHM sidecars when present
roots/<id>/...                     # Markdown, preserving its path below each root
manifest.json                      # original root paths, scopes, tags, and archive mapping
```

Configured roots can be unrelated physical paths. `manifest.json` records every configured
root's original absolute path and project/shared scope, while `roots/<id>/` keeps the
directory structure below that root. A reused physical root is stored once and mapped to
every relevant scope. Missing or unreadable roots, and files that cannot be read, do not
abort the archive; they are listed in both the result and manifest.

Use `agent-memory snapshot search ARCHIVE QUERY` to query the archived SQLite index without
restoring it. The command expands only `database/index.sqlite3` to a system temporary
directory, opens it read-only through the ordinary search logic, then removes the temporary
directory. It does not read or modify the active settings file, active SQLite database, or
Markdown sources. Project and tag filters (`--project`, repeated `--tag`, `--no-shared`, and
`--limit`) have the same meaning as live search.

Use `agent-memory snapshot inspect ARCHIVE` when the question is what the archive covers.
It reads `manifest.json` only and reports archived project scopes, memory roots, skipped
roots/files, and the number of Markdown files; it does not extract the database.

Agent Memory intentionally provides no automated restore command. Recovery is a human
decision: manually extract the archive, inspect `manifest.json`, compare its contents with
the current filesystem, and choose which files to copy. The original WAL/SHM sidecars are
preserved for inspection; `database/index.sqlite3` is the consistent database backup.

## Agent workflow

The intended progressive-disclosure loop is:

```text
actual work path
  -> resolve project (or global fallback for an unbound/ambiguous read-only query path)
  -> scoped search/list/tags/browse (+ optional hierarchical tag filters)
  -> brief + absolute source path + canonical full tags
  -> open/read the Markdown file
  -> act
  -> edit/create/delete Markdown only when durable knowledge changes
  -> sync
  -> inspect links/backlinks when dependencies matter
```

The skill should normally pass the actual project/worktree path, not merely `$PWD`, when
those differ.

## Reference projects

The implementation borrows design ideas, not source code, from:

- **Basic Memory** (`basicmachines-co/basic-memory`, AGPL-3.0): Markdown as durable source
  of truth, local-first indexing, project-aware memory, and wiki-link knowledge graph.
- **mnemos** (`arhuman/mnemos`, MIT): SQLite FTS5/BM25, cited file-oriented retrieval,
  absolute configuration anchoring, and explicit outbound/inbound link inspection.
- **Mooncite / claude-memory-mcp** (`WhenMoon-afk/claude-memory-mcp`): evidence-first
  principle that an index/locator should point back to inspectable physical source bytes.

No code is copied from those projects. This MVE intentionally stays smaller: Python
stdlib + SQLite + Markdown + one CLI/Skill contract.

## Follow-up features (not required for this PR)

1. `project add/remove` commands that edit `settings.json` safely instead of requiring
   manual JSON edits.
2. Incremental sync using stored hashes/mtimes and an optional watcher.
3. Heading/chunk-level indexing and line-range citations.
4. Optional semantic/hybrid search behind a local-only extra.
5. Optional MCP adapter exposing the same `resolve/search/list/links/sync` contract.
6. A conservative capture/consolidation workflow once manual file-first memory proves
   useful in daily multi-agent work.
