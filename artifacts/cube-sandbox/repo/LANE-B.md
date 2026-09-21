# Lane B report — CLI, extension diagnostics, and read-only safety

## What changed

- `memory_store_ext.search_documents()` now preserves lane A's
  `match_mode`, `matched_terms`, `missing_terms`, and `retrieval_routes`; it
  only adds extension metadata.
- Read-only open fallback now classifies SQLite failures by normalized base
  error code (`extended_code & 0xff`). The finite text fallback is used only
  when `sqlite_errorcode` is unavailable (Python 3.10 compatibility).
- Immutable mode is attempted only for CANTOPEN/READONLY with no nonempty WAL.
  A post-open WAL race recheck remains, and the connection is closed if the
  recheck fails. Unknown I/O and corrupt-database errors propagate.
- Corrupt/invalid indexes report the resolved DB path and instruct the user to
  stop writers, move DB/WAL/SHM together to quarantine, and explicitly sync.
  No files are moved and no command is run automatically.
- A Han query with bigrams warns on stderr when the auxiliary index is missing
  or outdated. The auxiliary tables are not in `REQUIRED_SCHEMA`, so old
  indexes remain queryable and the read path creates no schema.

## Red evidence before the change

Diagnostic reproduction used the existing unittest fixture and queried
`sandbox push pull permission` after indexing a document containing only
`sandbox push`. The base result was truthful, but the extension result was:

```text
base={... "match_mode": "relaxed", "matched_terms": ["sandbox", "push"],
      "missing_terms": ["pull", "permission"], "retrieval_routes": ["word"]}
ext ={... "match_mode": "strict",
      "matched_terms": ["sandbox", "push", "pull", "permission"],
      "missing_terms": [], "retrieval_routes": ["word"]}
exit=0
```

Cold read-only reproduction (initialized/synced DB, removed sidecars, files
`0444`, registry directory `0555`):

```text
sidecars=
agent-memory: attempt to write a readonly database
exit=2
```

Corrupt DB reproduction (`dd if=/dev/urandom ... bs=4096 count=1`, then
`--json status`):

```text
agent-memory: file is not a database
exit=2
```

## Green evidence after the change

### Diagnostics passthrough

The same fixture/query now returns:

```text
{"match_mode": "relaxed", "matched_terms": ["sandbox", "push"],
 "missing_terms": ["pull", "permission"], "retrieval_routes": ["word"], ...}
diagnostic_exit=0
```

### Cold DB without sidecars

Fresh initialized/synced DB; DB/settings `0444`, directory `0555`, and neither
WAL nor SHM present:

```text
sidecars=
agent-memory: warning: read-only WAL access unavailable; using an immutable index view
[]
exit=0
```

Fixture: `/tmp/am-b-green-cold.hU4BNN`.

### Safe live WAL + SHM

A writer remained open after committing a uniquely searchable row only to the
live WAL; WAL/SHM/DB/settings were `0444` and the registry directory `0555`.

```text
sidecars before: [('index.sqlite3-shm', 32768), ('index.sqlite3-wal', 24752)]
[
  {
    "title": "WAL probe",
    "brief": "walprobeuniqueterm",
    "match_mode": "strict",
    "retrieval_routes": ["word"]
  }
]
exit=0
```

Fixture: `/tmp/am-b-green-safe-wal.mFZFsl`.

### Unsafe nonempty WAL without SHM

A frozen copy contained the DB and the 24,752-byte nonempty WAL but omitted
SHM; its unique row existed only in that WAL.

```text
target sidecars: [('index.sqlite3-wal', 24752)]
agent-memory: read-only index has uncheckpointed WAL data; run: agent-memory sync in a writable environment
exit=2
```

Fixture: `/tmp/am-b-green-unsafe-wal.rsZVbr`.

### Corrupt DB recovery guidance

Fresh `--json status` after replacing the temporary DB with 4096 random bytes:

```text
agent-memory: database is corrupt or invalid: /tmp/am-b-green-corrupt.HcuOhw/registry/index.sqlite3 (file is not a database). Stop all writers; move /tmp/am-b-green-corrupt.HcuOhw/registry/index.sqlite3, /tmp/am-b-green-corrupt.HcuOhw/registry/index.sqlite3-wal, and /tmp/am-b-green-corrupt.HcuOhw/registry/index.sqlite3-shm together into a quarantine directory, then run: agent-memory sync. No files were moved.
exit=2
```

`--json doctor` also returned `doctor_failed`, the same recovery guidance and
the resolved DB in `paths`, with exit 2.

### Old/missing Han auxiliary index

After explicitly dropping `document_cjk_fts` and `derived_indexes` from a
temporary initialized index:

```text
agent-memory: warning: Han auxiliary index is missing or outdated; run: agent-memory sync
[]
exit=0
```

A subsequent read-only schema query returned `aux_tables_after_read=[]`, so the
search did not recreate either table.

### Error classification and unittest suite

```text
{'py310_text_fallback': True, 'unknown_code_not_swallowed': False,
 'extended_readonly_normalized': True}
```

```console
$ python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py' 2>&1 | tail -3
Ran 37 tests in 4.030s

OK
tests_exit=0
```

`python3 -m py_compile` and `git diff --check` also exited 0.

## Unresolved items

None within lane B's owned files and acceptance scope.

## HANDOFF

- Lane D owns the documentation files and should ensure the stale statement
  that all reads use a write/DDL connection is replaced with the current
  read-only/WAL behavior and grouped quarantine recovery wording.
- Integration should retain lane A's result fields unchanged and rerun these
  read-only cases after all lanes are merged.
