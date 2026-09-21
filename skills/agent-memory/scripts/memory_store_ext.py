"""Roadmap extensions layered over the file-first memory_store without replacing its MVE schema."""

from __future__ import annotations
import re, sqlite3, sys
from pathlib import Path
from typing import Any, Iterable
import memory_store as base
from memory_config import MemoryError, MemoryRoot
from memory_navigation import tier_sql_expression

MEMORY_ID_RE = re.compile(r"^mem_[A-Za-z0-9_-]+$")
LIFECYCLE_STATES = {"raw", "validated", "promoted", "superseded"}
REQUIRED_SCHEMA = {
    "documents",
    "document_scopes",
    "document_tags",
    "links",
    "document_fts",
    "memory_meta",
    "memory_links",
}
MEM_LINK_RE = re.compile(
    r"(?<!!)\[([^\]]*)\]\(memory://(mem_[A-Za-z0-9_-]+)(?:#([^)]+))?\)"
)


def _readonly_open_error(error: sqlite3.OperationalError) -> bool:
    """Return whether a normal read-only open may safely try immutable mode."""
    if hasattr(error, "sqlite_errorcode"):
        code = error.sqlite_errorcode
        return code is not None and code & 0xFF in {
            sqlite3.SQLITE_CANTOPEN,
            sqlite3.SQLITE_READONLY,
        }

    # Python 3.10 does not expose sqlite_errorcode. Keep this compatibility
    # list deliberately narrow so corruption and unknown I/O failures surface.
    return str(error).casefold() in {
        "unable to open database file",
        "attempt to write a readonly database",
        "database is read-only",
    }


def _wal_has_content(path: Path) -> bool:
    try:
        return path.stat().st_size > 0
    except FileNotFoundError:
        return False


def connect_db(path: Path) -> sqlite3.Connection:
    c = base.connect_db(path)
    c.executescript(
        """CREATE TABLE IF NOT EXISTS memory_meta(document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,memory_id TEXT,doc_type TEXT,lifecycle_status TEXT,promoted_to TEXT,superseded_by TEXT,tier TEXT);CREATE INDEX IF NOT EXISTS idx_memory_meta_id ON memory_meta(memory_id);CREATE TABLE IF NOT EXISTS memory_links(source_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,target_memory_id TEXT NOT NULL,label TEXT NOT NULL,anchor TEXT,PRIMARY KEY(source_document_id,target_memory_id,label));CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_memory_id);"""
    )
    columns = {row[1] for row in c.execute("PRAGMA table_info(memory_meta)")}
    if "tier" not in columns:
        c.execute("ALTER TABLE memory_meta ADD COLUMN tier TEXT")
    c.commit()
    return c


def connect_db_readonly(path: Path) -> tuple[sqlite3.Connection, bool]:
    """Open an initialized index without creating schema or journal sidecars.

    A WAL-mode database normally needs a writable ``-shm`` file even for a
    read-only connection.  Prefer normal read-only locking so concurrent WAL
    writers remain visible, then fall back to an immutable view only when the
    database has no uncheckpointed WAL content.
    """
    path = path.expanduser().resolve(strict=False)
    if not path.is_file():
        raise MemoryError("index not initialized, run: agent-memory sync")

    def open_uri(*, immutable: bool) -> sqlite3.Connection:
        suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
        conn = sqlite3.connect(f"{path.as_uri()}{suffix}", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA query_only=ON")
            # Force SQLite to open the WAL/shared-memory state now, so the caller
            # does not receive a delayed 'unable to open database file' error.
            conn.execute("PRAGMA schema_version").fetchone()
            return conn
        except Exception:
            conn.close()
            raise

    used_immutable = False
    try:
        conn = open_uri(immutable=False)
    except sqlite3.OperationalError as error:
        if not _readonly_open_error(error):
            raise
        wal_path = Path(f"{path}-wal")
        if _wal_has_content(wal_path):
            raise MemoryError(
                "read-only index has uncheckpointed WAL data; "
                "run: agent-memory sync in a writable environment"
            ) from error
        conn = open_uri(immutable=True)
        try:
            wal_changed = _wal_has_content(wal_path)
        except Exception:
            conn.close()
            raise
        if wal_changed:
            conn.close()
            raise MemoryError(
                "read-only index changed while opening; "
                "retry or run: agent-memory sync in a writable environment"
            ) from error
        used_immutable = True

    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
    except Exception:
        conn.close()
        raise
    if not REQUIRED_SCHEMA.issubset(tables):
        conn.close()
        raise MemoryError("index not initialized, run: agent-memory sync")
    return conn, used_immutable


def _meta(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    m, body = base._parse_frontmatter(text)
    nested_type = None
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        lines = text[4:end].splitlines() if end >= 0 else ()
        for index, line in enumerate(lines):
            key, _, value = line.partition(":")
            if line[:1].isspace() or key.strip() != "metadata" or value.strip():
                continue
            children = []
            for child in lines[index + 1 :]:
                if child and not child[0].isspace():
                    break
                if child.strip():
                    children.append(child)
            if not children:
                break
            direct_indent = min(len(child) - len(child.lstrip()) for child in children)
            for child in children:
                indent = len(child) - len(child.lstrip())
                if indent != direct_indent:
                    continue
                child_key, _, child_value = child.strip().partition(":")
                if child_key == "type":
                    nested_type = child_value.strip().strip("\"'") or None
                    break
            break
    return {
        "id": str(m.get("id") or "").strip() or None,
        "type": str(m.get("type") or nested_type or "").strip() or None,
        "status": str(m.get("status") or "").strip().lower() or None,
        "promoted_to": str(m.get("promoted_to") or "").strip() or None,
        "superseded_by": str(m.get("superseded_by") or "").strip() or None,
        "tier": str(m.get("tier") or "").strip().lower() or None,
    }, body


def sync_index(c: sqlite3.Connection, roots: list[MemoryRoot]):
    result = base.sync_index(c, roots)
    with c:
        c.execute("DELETE FROM memory_meta")
        c.execute("DELETE FROM memory_links")
        for row in c.execute("SELECT id,path FROM documents").fetchall():
            p = Path(row["path"])
            if not p.is_file():
                continue
            m, body = _meta(p)
            c.execute(
                "INSERT INTO memory_meta(document_id,memory_id,doc_type,lifecycle_status,promoted_to,superseded_by,tier) VALUES(?,?,?,?,?,?,?)",
                (
                    row["id"],
                    m["id"],
                    m["type"],
                    m["status"],
                    m["promoted_to"],
                    m["superseded_by"],
                    m["tier"],
                ),
            )
            for match in MEM_LINK_RE.finditer(body):
                c.execute(
                    "INSERT OR IGNORE INTO memory_links(source_document_id,target_memory_id,label,anchor) VALUES(?,?,?,?)",
                    (
                        row["id"],
                        match.group(2),
                        match.group(1).strip() or match.group(2),
                        match.group(3),
                    ),
                )
    return result


def _enrich(c, item):
    columns = {row[1] for row in c.execute("PRAGMA table_info(memory_meta)")}
    tier_select = ",tier" if "tier" in columns else ",NULL AS tier"
    row = c.execute(
        "SELECT memory_id,doc_type,lifecycle_status,promoted_to,superseded_by" + tier_select + " FROM memory_meta WHERE document_id=?",
        (item["id"],),
    ).fetchone()
    if row:
        item.update(
            memory_id=row[0],
            type=row[1],
            status=row[2],
            promoted_to=row[3],
            superseded_by=row[4],
            tier=row[5],
        )
    return item


def search_documents(c, query, project=None, tags=(), limit=10, include_shared=True):
    if base.han_bigrams((query,)) and not base._cjk_index_is_current(c):
        print(
            "agent-memory: warning: Han auxiliary index is missing or outdated; "
            "run: agent-memory sync",
            file=sys.stderr,
        )
    return [
        _enrich(c, item)
        for item in base.search_documents(
            c, query, project, tags, limit, include_shared
        )
    ]


def list_documents(
    c, project=None, tags=(), limit=100, include_shared=True, tier_filter=None
):
    if tier_filter is not None:
        sql = (
            "SELECT d.id,d.path,d.title,d.brief FROM documents d "
            "LEFT JOIN memory_meta m ON m.document_id=d.id WHERE 1=1"
        )
        params = []
        if project:
            scopes = [project] + (["_shared"] if include_shared else [])
            placeholders = ",".join("?" for _ in scopes)
            sql += (
                " AND EXISTS (SELECT 1 FROM document_scopes s "
                f"WHERE s.document_id=d.id AND s.scope IN ({placeholders}))"
            )
            params.extend(scopes)
        for tag in tags:
            sql = base._append_tag_filter(sql, params, tag)
        sql += f" AND {tier_sql_expression(c)}=?"
        sql += " ORDER BY d.mtime_ns DESC,d.path LIMIT ?"
        params.extend([tier_filter, limit])
        return [
            _enrich(c, base._doc_payload(c, row))
            for row in c.execute(sql, params)
        ]
    return [
        _enrich(c, x)
        for x in base.list_documents(c, project, tags, limit, include_shared)
    ]


def enumerate_sources(context):
    return base.enumerate_sources(context)


def navigation_inventory(context, filters=None):
    result = base.navigation_inventory(context, filters)
    connection = (
        context.get("connection", context.get("conn"))
        if isinstance(context, dict)
        else getattr(context, "connection", getattr(context, "conn", None))
    )
    if connection is not None:
        result["route_candidates"] = [
            _enrich(connection, item) for item in result["route_candidates"]
        ]
    return result


def resolve_document(c, ref):
    raw = ref[9:].split("#", 1)[0] if ref.startswith("memory://") else ref
    if raw.startswith("mem_"):
        rows = c.execute(
            "SELECT d.id,d.path,d.title,d.brief FROM memory_meta m JOIN documents d ON d.id=m.document_id WHERE m.memory_id=?",
            (raw,),
        ).fetchall()
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise MemoryError(f"duplicate memory id in index: {raw}")
    return base.resolve_document(c, ref)


def link_graph(c, ref):
    doc = resolve_document(c, ref)
    result = base.link_graph(c, str(doc["path"]))
    did = int(doc["id"])
    for r in c.execute(
        "SELECT target_memory_id,label,anchor FROM memory_links WHERE source_document_id=?",
        (did,),
    ):
        target = c.execute(
            "SELECT d.path,d.title FROM memory_meta m JOIN documents d ON d.id=m.document_id WHERE m.memory_id=?",
            (r[0],),
        ).fetchone()
        result["outbound"].append(
            {
                "label": r[1],
                "href": f"memory://{r[0]}",
                "anchor": r[2],
                "path": target[0] if target else None,
                "title": target[1] if target else None,
                "memory_id": r[0],
                "resolved": target is not None,
            }
        )
    mid = c.execute(
        "SELECT memory_id FROM memory_meta WHERE document_id=?", (did,)
    ).fetchone()
    if mid and mid[0]:
        for r in c.execute(
            "SELECT s.id,s.path,s.title,l.label,l.anchor FROM memory_links l JOIN documents s ON s.id=l.source_document_id WHERE l.target_memory_id=?",
            (mid[0],),
        ):
            result["inbound"].append(
                {
                    "id": r[0],
                    "title": r[2],
                    "path": r[1],
                    "label": r[3],
                    "href": f"memory://{mid[0]}",
                    "anchor": r[4],
                }
            )
    result["document"] = _enrich(c, result["document"])
    return result


def status(c, settings_path, db_path):
    x = base.status(c, settings_path, db_path)
    x["stable_ids"] = c.execute(
        "SELECT count(*) FROM memory_meta WHERE memory_id IS NOT NULL"
    ).fetchone()[0]
    return x
