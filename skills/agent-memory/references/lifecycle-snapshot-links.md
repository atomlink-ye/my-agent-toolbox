# Lifecycle, snapshot, and links (low-frequency machinery)

These operations are not substitutes for the normal brief-first consumption loop. Start
with the current scope map, read a routed topic, and enter this reference only when that
topic or the task specifically calls for lifecycle, snapshot, or link work.

These subcommands exist and work, but in real historical usage across 1038 logged
`agent-memory` invocations, `lifecycle` was called 1 time, `links` 2 times, and
`snapshot` 0 times. Do not reach for this section on a normal recall/capture loop;
read it only when a task specifically calls for consolidating duplicate memories,
retiring a canonical rule, or taking a pre-destructive backup.

## Lifecycle and stable memory identity

Every successful capture writes an opaque `mem_<uuid>` frontmatter `id` and returns it as
`memory_id`; its default lifecycle status is `raw`. Keep that ID when the same durable
knowledge must be referenced across project roots or after the Markdown file moves. A
Markdown link such as `[canonical rule](memory://mem_abc)` is a stable reference, and
`agent-memory links mem_abc` resolves its outbound links and backlinks.

Use lifecycle only when the knowledge's reuse status materially changes, not for ordinary
edits, tag changes, or one-off notes. The states are:

- `raw`: newly captured evidence that has not yet been independently confirmed;
- `validated`: evidence whose guidance has worked or been checked again;
- `promoted`: evidence retained as provenance but pointing at a canonical reusable memory;
- `superseded`: an older or duplicate memory retained as provenance but replaced by a newer
  canonical memory.

The usual path is `raw` → `validated`; after repeated related evidence has been consolidated,
create or identify the canonical memory, promote the evidence to it, and supersede the other
duplicates to that same target. The command makes an explicit source edit and re-indexes:

```sh
# The capture result supplies mem_evidence and mem_canonical.
agent-memory lifecycle mem_evidence validated
agent-memory lifecycle mem_evidence promoted --target mem_canonical
agent-memory lifecycle mem_old superseded --target memory://mem_canonical
```

`promoted` requires `--target` and writes `promoted_to: memory://…`; `superseded` likewise
requires `--target` and writes `superseded_by: memory://…`. Doctor checks duplicate or
malformed IDs, invalid lifecycle values, and unresolved lifecycle or `memory://` targets.
Legacy Markdown without an ID remains valid; Doctor reports it as migration information,
not a reason to manufacture lifecycle transitions.

## Promote instead of duplicating

A captured learning starts as evidence, not automatically as a global rule. When it is
repeatedly useful:

1. keep the original learning Markdown as provenance;
2. update the canonical workflow/domain/architecture document that should own the rule;
3. link the learning to that document with a normal Markdown link;
4. use `agent-memory links` to retain the backlink trail.

This lets multiple projects cite shared domain knowledge without flattening their project
memory scopes.

## Links

Link related memory with ordinary Markdown links such as
`[Shared review workflow](../../shared/workflows/review.md)`; `[[relative-note]]`
wikilinks are also indexed. Run `sync` after manually creating, moving, deleting, or
materially editing files, and use `links <file>` when backlinks or cross-project
dependencies matter.

## Snapshot before destructive bulk work

Before a bulk Markdown edit without another rollback mechanism, create one portable,
read-only archive of the configured memory sources and index:

```sh
agent-memory --json snapshot
agent-memory --json snapshot --output /abs/path/to/backup-directory
agent-memory --json snapshot search /abs/path/to/agent-memory-20260820T120000Z.tar.gz "sandbox ownership"
agent-memory --json snapshot inspect /abs/path/to/agent-memory-20260820T120000Z.tar.gz
```

The default destination is `snapshots/` beside the active settings file. The archive keeps
`settings.json`, a consistent SQLite copy, any present SQLite WAL/SHM sidecars, and every
Markdown file under each configured root. `manifest.json` maps each archive root back to
its original absolute path and scope, so unrelated roots are never flattened together.
Missing or unreadable roots are skipped and returned in the result; sources and settings
are never changed and snapshot does not run `sync`.

`snapshot search ARCHIVE QUERY` opens only the archived SQLite index in a temporary
directory and returns matching memories without touching the live registry. `snapshot
inspect ARCHIVE` reads only the manifest and reports the archived projects and memory roots.
Temporary extraction is removed at command exit.

Agent Memory deliberately provides no automatic restore command. To recover, manually
extract the archive, inspect `manifest.json`, and decide yourself how to handle existing
files before copying any source back.

Use an external cron/launchd scheduler when periodic snapshots are wanted. Agent Memory
only performs one archive operation per invocation.
