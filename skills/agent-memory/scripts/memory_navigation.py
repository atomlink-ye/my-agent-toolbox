"""Small, truthful SQLite inventories for memory navigation."""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable

from memory_config import SHARED_SCOPE, _normalize_tag


def _tag_predicates(tags: tuple[str, ...], alias: str = "d") -> tuple[str, list[Any]]:
    sql = ""
    params: list[Any] = []
    for raw_tag in tags:
        segments = _normalize_tag(raw_tag).split(":")
        tests = [
            "instr(':' || lower(tf.tag) || ':', ':' || lower(?) || ':') > 0"
            for _ in segments
        ]
        sql += (
            f" AND EXISTS (SELECT 1 FROM document_tags tf WHERE tf.document_id={alias}.id AND "
            + " AND ".join(tests)
            + ")"
        )
        params.extend(segments)
    return sql, params


def _scope_clause(project: str | None, include_shared: bool) -> tuple[str, list[Any]]:
    scopes = ([project] if project else []) + ([SHARED_SCOPE] if include_shared else [])
    if not scopes:
        return " AND 0", []
    marks = ",".join("?" for _ in scopes)
    return (
        " AND EXISTS (SELECT 1 FROM document_scopes sv "
        f"WHERE sv.document_id=d.id AND sv.scope IN ({marks}))",
        scopes,
    )


def _count(conn: sqlite3.Connection, scope: str, tags: tuple[str, ...]) -> int:
    tag_sql, tag_params = _tag_predicates(tags)
    row = conn.execute(
        "SELECT count(DISTINCT d.id) FROM documents d "
        "WHERE EXISTS (SELECT 1 FROM document_scopes s "
        "WHERE s.document_id=d.id AND s.scope=?)" + tag_sql,
        [scope, *tag_params],
    ).fetchone()
    return int(row[0])


def _tier_column(conn: sqlite3.Connection) -> str:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_meta)")}
    return "m.tier AS tier" if "tier" in columns else "NULL AS tier"


def _tier(
    explicit_tier: Any, doc_type: Any, lifecycle_status: Any
) -> tuple[str, bool]:
    """Classify from lifecycle/type metadata; unknown combinations default to archive."""
    tier = str(explicit_tier or "").strip().casefold()
    status = str(lifecycle_status or "").strip().casefold()
    kind = str(doc_type or "").strip().casefold()
    if tier in {"core", "archive"}:
        return tier, False
    if status in {"validated", "promoted"}:
        return "core", False
    if status in {"raw", "superseded"}:
        return "archive", False
    if kind in {"user", "feedback"}:
        return "core", False
    return "archive", True


def _visible_tier_counts(
    conn: sqlite3.Connection,
    project: str | None,
    tags: tuple[str, ...],
    include_shared: bool,
) -> dict[str, int]:
    visible_sql, visible_params = _scope_clause(project, include_shared)
    tag_sql, tag_params = _tag_predicates(tags)
    tier_column = _tier_column(conn)
    rows = conn.execute(
        "SELECT DISTINCT d.id,m.doc_type,m.lifecycle_status," + tier_column + " FROM documents d "
        "LEFT JOIN memory_meta m ON m.document_id=d.id WHERE 1=1"
        + visible_sql
        + tag_sql,
        [*visible_params, *tag_params],
    ).fetchall()
    counts = {"core": 0, "archive": 0, "defaulted": 0}
    for row in rows:
        tier, defaulted = _tier(row["tier"], row["doc_type"], row["lifecycle_status"])
        counts[tier] += 1
        counts["defaulted"] += int(defaulted)
    return counts


def _sample(
    conn: sqlite3.Connection,
    scope: str,
    tags: tuple[str, ...],
    limit: int,
    context_terms: tuple[str, ...] = (),
    tier_filter: str | None = None,
    exclude_ids: Iterable[int] = (),
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    tag_sql, tag_params = _tag_predicates(tags)
    tier_column = _tier_column(conn)
    rows = conn.execute(
        "SELECT d.id,d.title,d.brief,d.path,d.mtime_ns,m.doc_type,m.lifecycle_status,"
        + tier_column + ","
        "(SELECT group_concat(t.tag,' ') FROM document_tags t WHERE t.document_id=d.id) AS tags "
        "FROM documents d LEFT JOIN memory_meta m ON m.document_id=d.id "
        "WHERE EXISTS (SELECT 1 FROM document_scopes s "
        "WHERE s.document_id=d.id AND s.scope=?)" + tag_sql
        + " ORDER BY d.mtime_ns DESC,d.path",
        [scope, *tag_params],
    ).fetchall()
    excluded = set(exclude_ids)
    if excluded:
        rows = [row for row in rows if int(row["id"]) not in excluded]
    if tier_filter is not None:
        rows = [
            row
            for row in rows
            if _tier(row["tier"], row["doc_type"], row["lifecycle_status"])[0] == tier_filter
        ]
    terms = {
        token
        for term in context_terms
        for token in re.findall(r"[^\W_]+", term.casefold(), flags=re.UNICODE)
    }

    def rank(row: sqlite3.Row) -> tuple[int, int, str, str]:
        metadata = " ".join(
            str(row[name] or "") for name in ("title", "brief", "tags", "path")
        ).casefold()
        candidate_terms = set(
            re.findall(r"[^\W_]+", metadata, flags=re.UNICODE)
        )
        relevance = len(terms & candidate_terms)
        return (
            -relevance,
            -int(row["mtime_ns"]),
            str(row["path"]).casefold(),
            str(row["path"]),
        )

    rows = sorted(rows, key=rank)[:limit]
    return [
        {
            "id": int(row["id"]),
            "title": row["title"],
            "brief": row["brief"],
            "path": row["path"],
            "scope": scope,
        }
        for row in rows
    ]


def navigation_inventory(
    conn: sqlite3.Connection,
    *,
    project: str | None,
    tags: Iterable[str] = (),
    include_shared: bool = True,
    global_limit: int = 2,
    project_limit: int = 4,
    tag_limit: int = 5,
    context_terms: Iterable[str] = (),
    tier_filter: str | None = None,
) -> dict[str, Any]:
    """Return independently counted scope components plus a bounded index."""
    tags = tuple(tags)
    visible_sql, visible_params = _scope_clause(project, include_shared)
    tag_sql, tag_params = _tag_predicates(tags)
    visible = int(
        conn.execute(
            "SELECT count(DISTINCT d.id) FROM documents d WHERE 1=1"
            + visible_sql
            + tag_sql,
            [*visible_params, *tag_params],
        ).fetchone()[0]
    )
    global_count = _count(conn, SHARED_SCOPE, tags) if include_shared else 0
    project_count = _count(conn, project, tags) if project is not None else None
    tier_counts = _visible_tier_counts(conn, project, tags, include_shared)

    tag_rows = conn.execute(
        "SELECT t.tag,count(DISTINCT t.document_id) AS count "
        "FROM document_tags t JOIN documents d ON d.id=t.document_id WHERE 1=1"
        + visible_sql
        + tag_sql
        + " GROUP BY t.tag ORDER BY count DESC,lower(t.tag),t.tag LIMIT ?",
        [*visible_params, *tag_params, max(0, tag_limit)],
    )
    top_tags = [{"tag": row["tag"], "count": int(row["count"])} for row in tag_rows]
    context_terms = tuple(context_terms)
    project_rows = (
        _sample(conn, project, tags, project_limit, context_terms, tier_filter)
        if project
        else []
    )
    project_ids = {row["id"] for row in project_rows}
    global_rows = (
        _sample(
            conn,
            SHARED_SCOPE,
            tags,
            global_limit,
            context_terms,
            tier_filter,
            exclude_ids=project_ids,
        )
        if include_shared
        else []
    )
    project_summaries = [
        {"project": str(row["scope"]), "count": int(row["count"])}
        for row in conn.execute(
            "SELECT s.scope,count(DISTINCT d.id) AS count "
            "FROM document_scopes s JOIN documents d ON d.id=s.document_id "
            "WHERE s.scope<>?" + tag_sql
            + " GROUP BY s.scope ORDER BY lower(s.scope),s.scope",
            [SHARED_SCOPE, *tag_params],
        )
    ]
    shown = {row["id"] for row in [*global_rows, *project_rows]}
    return {
        "counts": {
            "distinct": visible,
            "global": global_count,
            "project": project_count,
            "components_overlap": True,
            **tier_counts,
        },
        "tags": top_tags,
        "global": global_rows,
        "project": project_rows,
        "project_summaries": project_summaries,
        "omitted": max(0, visible - len(shown)),
        "diagnostics": (
            [
                {
                    "code": "tier_default_archive",
                    "severity": "info",
                    "count": tier_counts["defaulted"],
                }
            ]
            if tier_counts["defaulted"]
            else []
        ),
    }
