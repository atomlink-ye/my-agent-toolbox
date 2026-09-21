#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from pathlib import Path
from memory_admin import preferred_capture_root, project_inventory, tag_inventory
from memory_doctor_ext import doctor
from memory_capture import KINDS, LIFECYCLE_STATES, capture_memory
from memory_config import (
    AmbiguousDescendantBindingError,
    MemoryError,
    UnboundPathError,
    collect_memory_roots,
    database_path,
    default_settings_path,
    flatten_bindings,
    init_settings,
    load_settings,
    resolve_binding,
    resolve_project_binding,
    shared_roots,
)
from memory_format import compact_dump, table_dump, yaml_dump
from memory_lifecycle import update_lifecycle
from memory_snapshot import inspect_snapshot, search_snapshot, snapshot_registry
from memory_store_ext import (
    connect_db,
    connect_db_readonly,
    link_graph,
    list_documents,
    search_documents,
    status,
    sync_index,
)


READ_ONLY_DB_COMMANDS = {
    "status",
    "search",
    "list",
    "links",
    "projects",
    "tags",
    "browse",
}


def _sqlite_base_error_code(error: sqlite3.Error):
    code = getattr(error, "sqlite_errorcode", None)
    return None if code is None else code & 0xFF


def _database_error_message(error: Exception, db: Path | None) -> str:
    corrupt = False
    if isinstance(error, sqlite3.Error):
        if hasattr(error, "sqlite_errorcode"):
            corrupt = _sqlite_base_error_code(error) in {
                sqlite3.SQLITE_CORRUPT,
                sqlite3.SQLITE_NOTADB,
            }
        else:
            corrupt = str(error).casefold() in {
                "database disk image is malformed",
                "file is not a database",
            }
    if not corrupt or db is None:
        return str(error)

    db = db.expanduser().resolve(strict=False)
    return (
        f"database is corrupt or invalid: {db} ({error}). Stop all writers; "
        f"move {db}, {db}-wal, and {db}-shm together into a quarantine "
        "directory, then run: agent-memory sync. No files were moved."
    )


def _readonly_connection(db: Path):
    connection, used_immutable = connect_db_readonly(db)
    if used_immutable:
        print(
            "agent-memory: warning: read-only WAL access unavailable; "
            "using an immutable index view",
            file=sys.stderr,
        )
    return connection


def _human_links(result):
    document = result.get("document")
    if isinstance(document, dict):
        print(f"{document['title']}\n  {document['path']}")
    for direction in ("outbound", "inbound"):
        print(f"{direction}:")
        links = result.get(direction, [])
        if not links:
            print("  - (none)")
            continue
        for link in links:
            if direction == "outbound":
                marker = "ok" if link.get("resolved") else "dangling"
                print(f"  - [{marker}] {link['label']} -> {link.get('path') or '-'}")
            else:
                print(f"  - {link['title']} <- {link['path']}")


def _slim_json(value, key=None):
    """Drop empty JSON fields and bound diagnostic score precision."""
    if isinstance(value, dict):
        return {
            child_key: _slim_json(child, child_key)
            for child_key, child in value.items()
            if child is not None and child != "" and child != []
        }
    if isinstance(value, list):
        return [_slim_json(child) for child in value]
    if key == "score" and isinstance(value, float):
        return round(value, 6)
    return value


def _emit(r, cmd, fmt, verbose=False, memory_roots=()):
    if fmt == "json":
        print(
            json.dumps(
                r if verbose else _slim_json(r),
                indent=2 if verbose else None,
                ensure_ascii=False,
                separators=None if verbose else (",", ":"),
            )
        )
        return
    if fmt == "compact":
        print(compact_dump(r, memory_roots))
        return
    if fmt == "table":
        print(table_dump(cmd, r))
        return
    if fmt == "yaml":
        print(yaml_dump(r))
        return
    if cmd in {"search", "list"}:
        for x in r:
            print(
                f"[{x.get('memory_id') or x['id']}] {x['title']}\n  {x.get('brief','')}\n  path: {x['path']}\n  tags: {','.join(x.get('tags',[])) or '-'}"
            )
    elif cmd == "tags":
        for x in r:
            print(f"{x['tag']}  {x['count']}")
    elif cmd == "projects":
        for x in r:
            print(
                f"{x['project']} documents={x['documents']}\n  path: {x['path']}\n  capture: {x.get('capture_root') or '-'}"
            )
    elif cmd == "links":
        _human_links(r)
    elif cmd == "browse":
        for group in r["groups"]:
            label = (
                "[shared]"
                if group["project"] == "_shared"
                else f"[project: {group['project']}]"
            )
            print(label)
            documents = group["documents"]
            if not documents:
                print("  - (none)")
                continue
            for document in documents:
                print(
                    f"  - {document['title']}\n"
                    f"    {document.get('brief', '')}\n"
                    f"    path: {document['path']}"
                )
    else:
        print(json.dumps(r, indent=2, ensure_ascii=False))


def _opts(p):
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--compact",
        dest="output_format",
        action="store_const",
        const="compact",
        default=argparse.SUPPRESS,
    )
    g.add_argument(
        "--json",
        dest="output_format",
        action="store_const",
        const="json",
        default=argparse.SUPPRESS,
    )
    g.add_argument(
        "--table",
        dest="output_format",
        action="store_const",
        const="table",
        default=argparse.SUPPRESS,
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit the complete pretty-printed JSON diagnostics",
    )
    g.add_argument(
        "--text",
        dest="output_format",
        action="store_const",
        const="text",
        default=argparse.SUPPRESS,
    )
    g.add_argument(
        "--yaml",
        dest="output_format",
        action="store_const",
        const="yaml",
        default=argparse.SUPPRESS,
    )


def build_parser():
    p = argparse.ArgumentParser(prog="agent-memory")
    p.add_argument("--settings", type=Path, default=default_settings_path())
    _opts(p)
    p.set_defaults(output_format=None, verbose=False)
    s = p.add_subparsers(dest="command", required=True)

    def sub(n, h):
        q = s.add_parser(n, help=h)
        _opts(q)
        return q

    q = sub("init", "create settings")
    q.add_argument("--force", action="store_true")
    sub("status", "registry status")
    sub("sync", "re-index Markdown")
    q = sub("snapshot", "archive or inspect Agent Memory snapshots")
    q.add_argument(
        "--output",
        type=Path,
        help="directory for a generated .tar.gz (default: settings sibling snapshots/)",
    )
    snapshot_actions = q.add_subparsers(dest="snapshot_action")
    snapshot_inspect = snapshot_actions.add_parser(
        "inspect", help="show the project/root inventory recorded in a snapshot"
    )
    _opts(snapshot_inspect)
    snapshot_inspect.add_argument("archive", type=Path)
    snapshot_search = snapshot_actions.add_parser(
        "search", help="search a snapshot SQLite index without restoring it"
    )
    _opts(snapshot_search)
    snapshot_search.add_argument("archive", type=Path)
    snapshot_search.add_argument("query")
    snapshot_search.add_argument("--project")
    snapshot_search.add_argument("--tag", action="append", default=[])
    snapshot_search.add_argument("--limit", type=int, default=10)
    snapshot_search.add_argument("--no-shared", action="store_true")
    q = sub("resolve", "resolve path")
    q.add_argument("--path", type=Path, default=Path.cwd())
    for n in ("search", "list"):
        q = sub(n, n + " memory")
        if n == "search":
            q.add_argument("query")
        q.add_argument("--project")
        q.add_argument("--path", type=Path)
        q.add_argument("--tag", action="append", default=[])
        q.add_argument("--limit", type=int, default=10 if n == "search" else 100)
        q.add_argument("--no-shared", action="store_true")
    q = sub("links", "show links/backlinks")
    q.add_argument("document")
    q = sub("capture", "capture durable learning")
    q.add_argument("kind", choices=sorted(KINDS))
    q.add_argument("summary")
    target = q.add_mutually_exclusive_group()
    target.add_argument("--path", type=Path)
    target.add_argument("--project")
    q.add_argument("--details", default="")
    q.add_argument("--action", default="")
    q.add_argument("--tag", action="append", default=[])
    q.add_argument("--related", action="append", default=[])
    q.add_argument("--root")
    q.add_argument("--status", choices=sorted(LIFECYCLE_STATES), default="raw")
    q.add_argument("--allow-duplicate", action="store_true")
    q = sub("lifecycle", "change lifecycle")
    q.add_argument("document")
    q.add_argument("status", choices=sorted(LIFECYCLE_STATES))
    q.add_argument("--target")
    sub("projects", "list projects")
    q = sub("tags", "list tags")
    q.add_argument("--project")
    q.add_argument("--path", type=Path)
    q.add_argument("--no-shared", action="store_true")
    q = sub("browse", "browse memories grouped by shared/project")
    q.add_argument("--project")
    q.add_argument("--path", type=Path)
    q.add_argument("--tag", action="append", default=[])
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--no-shared", action="store_true")
    q = sub("doctor", "health diagnostics")
    q.add_argument("--path", type=Path)
    return p


def _fail(code, msg, path=None):
    return {
        "status": "error",
        "summary": {
            "errors": 1,
            "warnings": 0,
            "info": 0,
            "bindings": 0,
            "documents": 0,
            "dangling_links": 0,
            "unindexed_markdown": 0,
            "stale_documents": 0,
        },
        "resolved": None,
        "checks": [
            {
                "code": code,
                "severity": "error",
                "message": msg,
                **({"paths": [str(path)]} if path else {}),
            }
        ],
    }


def _query_project(settings, project=None, path=None):
    """Resolve the optional project scope used by read-only queries."""
    if project and path:
        raise MemoryError("use either --project or --path, not both")
    if project:
        return project
    if path is None:
        return None
    try:
        return resolve_binding(settings, path).project
    except (UnboundPathError, AmbiguousDescendantBindingError):
        return None


def _browse_documents(conn, project=None, tags=(), limit=100, include_shared=True):
    """Return visible documents grouped by their configured scope for people."""
    documents = list_documents(conn, project, tags, limit, include_shared)
    groups: dict[str, list[dict]] = {}
    for document in documents:
        for scope in document.get("projects", []):
            if scope == "_shared" and not include_shared:
                continue
            if project is not None and scope not in {project, "_shared"}:
                continue
            groups.setdefault(scope, []).append(document)
    ordered = ["_shared"] + sorted(scope for scope in groups if scope != "_shared")
    return {
        "groups": [
            {"project": scope, "documents": groups[scope]}
            for scope in ordered
            if scope in groups
        ]
    }


def main(argv=None):
    p = build_parser()
    raw = sys.argv[1:] if argv is None else argv
    if len({x for x in raw if x in {"--compact", "--json", "--table", "--text", "--yaml"}}) > 1:
        p.error("output options are mutually exclusive")
    a = p.parse_args(raw)
    if a.output_format == "compact" and a.command not in {"search", "list"}:
        p.error("--compact is only available for search and list")
    if a.verbose and a.output_format not in {None, "json"}:
        p.error("--verbose may only be used with --json")
    a.output_format = a.output_format or (
        "json"
        if a.verbose
        else "compact"
        if a.command in {"search", "list"}
        else "text"
        if a.command == "browse"
        else "yaml"
    )
    sp = a.settings.expanduser().resolve(strict=False)
    if a.command == "init":
        try:
            init_settings(sp, a.force)
            r = {"settings": str(sp)}
            _emit(r, "init", a.output_format, a.verbose)
            return 0
        except (MemoryError, OSError) as e:
            print(f"agent-memory: {e}", file=sys.stderr)
            return 2
    if a.command == "doctor":
        try:
            settings = load_settings(sp)
            db = database_path(settings, sp)
        except (MemoryError, OSError) as e:
            r = _fail("settings_invalid", str(e), sp)
            _emit(r, "doctor", a.output_format, a.verbose)
            return 2
        if not db.exists():
            r = _fail(
                "database_missing",
                "SQLite index does not exist; run agent-memory sync first",
                db,
            )
            _emit(r, "doctor", a.output_format, a.verbose)
            return 2
        try:
            c = _readonly_connection(db)
            r = doctor(c, settings, sp, db, a.path)
            c.close()
            _emit(r, "doctor", a.output_format, a.verbose)
            return 2 if r["status"] == "error" else 1 if r["status"] == "warn" else 0
        except Exception as e:
            r = _fail("doctor_failed", _database_error_message(e, db), db)
            _emit(r, "doctor", a.output_format, a.verbose)
            return 2
    if a.command == "snapshot":
        try:
            if a.snapshot_action == "inspect":
                r = inspect_snapshot(a.archive)
            elif a.snapshot_action == "search":
                r = search_snapshot(
                    a.archive,
                    a.query,
                    project=a.project,
                    tags=tuple(a.tag),
                    limit=a.limit,
                    include_shared=not a.no_shared,
                )
            else:
                settings = load_settings(sp)
                db = database_path(settings, sp)
                r = snapshot_registry(
                    settings,
                    sp,
                    db,
                    collect_memory_roots(settings, sp),
                    a.output,
                )
            _emit(r, "snapshot", a.output_format, a.verbose)
            return 0
        except (MemoryError, OSError, sqlite3.Error) as e:
            print(f"agent-memory: {e}", file=sys.stderr)
            return 2
    db: Path | None = None
    try:
        settings = load_settings(sp)
        db = database_path(settings, sp)
        if a.command in {"search", "list", "tags", "browse"}:
            # Validate all routing config before a query opens/initializes SQLite.
            flatten_bindings(settings)
            shared_roots(settings, sp)
        if a.command == "resolve":
            b = resolve_binding(settings, a.path)
            pref = preferred_capture_root(settings, b)
            r = {
                "path": str(a.path.resolve(strict=False)),
                "project": b.project,
                "binding": str(b.path),
                "memory": [
                    {"path": str(x.path), "tags": list(x.tags)}
                    for x in b.memory_roots
                ],
                "capture_root": (
                    str(pref)
                    if pref
                    else (
                        str(b.memory_roots[0].path)
                        if len(b.memory_roots) == 1
                        else None
                    )
                ),
                "shared": [str(x.path) for x in shared_roots(settings, sp)],
                "tags": list(b.tags),
            }
            _emit(r, a.command, a.output_format, a.verbose)
            return 0
        c = (
            _readonly_connection(db)
            if a.command in READ_ONLY_DB_COMMANDS
            else connect_db(db)
        )
        if a.command == "status":
            r = status(c, sp, db)
        elif a.command == "sync":
            r = sync_index(c, collect_memory_roots(settings, sp))
        elif a.command in {"search", "list"}:
            project = _query_project(settings, a.project, a.path)
            r = (
                search_documents(c, a.query, project, a.tag, a.limit, not a.no_shared)
                if a.command == "search"
                else list_documents(c, project, a.tag, a.limit, not a.no_shared)
            )
        elif a.command == "links":
            r = link_graph(c, a.document)
        elif a.command == "capture":
            b = (
                resolve_project_binding(settings, a.project)
                if a.project
                else resolve_binding(settings, a.path or Path.cwd())
            )
            pref = preferred_capture_root(settings, b)
            r = capture_memory(
                b,
                a.kind,
                a.summary,
                details=a.details,
                suggested_action=a.action,
                tags=a.tag,
                related=a.related,
                root=a.root or (str(pref) if pref else None),
                status=a.status,
                allow_duplicate=a.allow_duplicate,
            )
            r.update(
                {"sync": sync_index(c, collect_memory_roots(settings, sp))}
                if r.get("created")
                else {}
            )
        elif a.command == "lifecycle":
            r = update_lifecycle(c, a.document, a.status, target=a.target)
            r["sync"] = sync_index(c, collect_memory_roots(settings, sp))
        elif a.command == "projects":
            r = project_inventory(c, settings)
        elif a.command == "tags":
            project = _query_project(settings, a.project, a.path)
            r = tag_inventory(c, project, not a.no_shared)
        elif a.command == "browse":
            project = _query_project(settings, a.project, a.path)
            r = _browse_documents(c, project, a.tag, a.limit, not a.no_shared)
        roots = (
            [root.path for root in collect_memory_roots(settings, sp)]
            if a.command in {"search", "list"}
            else ()
        )
        _emit(r, a.command, a.output_format, a.verbose, roots)
        c.close()
        return 0
    except (MemoryError, OSError, sqlite3.Error) as e:
        print(f"agent-memory: {_database_error_message(e, db)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
