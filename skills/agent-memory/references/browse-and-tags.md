# Browse, tags, and manual discovery

## Discovery before guessing taxonomy

```sh
agent-memory projects
agent-memory browse --path /abs/path/to/workspace
agent-memory tags --path /abs/path/to/project
```

`projects` reports configured project paths, roots, capture roots, and indexed document
counts. `browse` is the human-first inventory: it groups memories under `[shared]` and
`[project: <name>]`, showing each title, brief, and absolute source path. It accepts `--project`, `--path`,
repeated `--tag`, `--limit`, and `--no-shared`; use `--json`, `--table`, or `--yaml` when
text is not wanted. `tags` reports canonical tags and usage counts and can be scoped with
`--path` or `--project`.

## Hierarchical tags and symmetric recall

Use `:` to keep a canonical classification path such as:

```text
agent-server:learnings
agent-server:operations:deploy
workflow:review
```

Storage/display remains canonical, but recall does not require hierarchy order:

- `--tag operations` matches `agent-server:operations:deploy`;
- `--tag agent-server:operations` matches it;
- `--tag deploy:agent-server` also matches it because every requested complete segment is present;
- partial strings such as `ops` do not match `operations`.

Normal `search` is tag-aware too. Tag aliases are added only to the disposable FTS index,
not to Markdown metadata. Therefore a document tagged `agent-server:learnings` can be
recalled with either `search "agent server learnings"` or `search "learnings agent server"`.
Hyphenated tag segments also contribute word aliases (`agent-server` -> `agent`, `server`).
Results still show only the full canonical tag.

Multiple explicit `--tag` filters are ANDed. Tags classify knowledge; project visibility
still comes only from settings scopes.

## Recall via list/tags instead of search

```sh
agent-memory search "learnings agent server" --path /abs/path/to/agent-server
agent-memory list --path /abs/path/to/agent-server --tag learnings
agent-memory list --path /abs/path/to/agent-server --tag learnings:agent-server
```

A stored tag such as `agent-server:learnings` is displayed in full even when the query is
reversed or uses only the `learnings` segment. Tag order is a classification convention,
not a recall requirement.
