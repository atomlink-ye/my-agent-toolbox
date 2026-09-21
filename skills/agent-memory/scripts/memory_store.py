"""SQLite index, Markdown metadata, and link graph for Agent Memory."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

from memory_cjk import (
    CJK_INDEX_NAME,
    CJK_INDEX_VERSION,
    han_bigrams,
    han_runs,
)
from memory_config import (
    MemoryError,
    MemoryRoot,
    SHARED_SCOPE,
    _dedupe,
    _expand_path,
    _normalize_tag,
    _normalize_tags,
)

MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]")

# Generated indexes and recovery copies are navigation infrastructure, not memory
# topics.  Keep this list exact so a legitimate note such as backup-strategy.md is
# still indexed.
MANAGEMENT_DIRECTORY_NAMES = frozenset(
    {".cache", ".backup", "cache", "caches", "context-cache", "backup", "backups"}
)
MANAGEMENT_FILE_NAMES = frozenset({"memory.md"})

# Deliberately small and conventional: these words add little discrimination to a
# natural-language OR fallback.  This is only consulted after the unchanged AND
# route is empty, and only plain alphabetic words are eligible.
NATURAL_LANGUAGE_STOPWORDS = frozenset(
    {
        "a",
        "after",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "before",
        "because",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "do",
        "does",
        "during",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "may",
        "might",
        "must",
        "of",
        "on",
        "or",
        "shall",
        "should",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "why",
        "will",
        "with",
        "would",
    }
)


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            brief TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_scopes (
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            PRIMARY KEY (document_id, scope)
        );
        CREATE TABLE IF NOT EXISTS document_tags (
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            PRIMARY KEY (document_id, tag)
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY,
            source_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            target_path TEXT NOT NULL,
            target_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            href TEXT NOT NULL,
            label TEXT NOT NULL,
            anchor TEXT,
            UNIQUE(source_document_id, href, label)
        );
        CREATE INDEX IF NOT EXISTS idx_scopes_scope ON document_scopes(scope);
        CREATE INDEX IF NOT EXISTS idx_tags_tag ON document_tags(tag);
        CREATE INDEX IF NOT EXISTS idx_links_target_path ON links(target_path);
        """)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(title, brief, content)"
    )
    _ensure_cjk_schema(conn)
    return conn


def _ensure_cjk_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS derived_indexes (name TEXT PRIMARY KEY, version INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS document_cjk_fts USING fts5(grams)"
    )


def _cjk_index_is_current(conn: sqlite3.Connection) -> bool:
    """Inspect derived state without attempting schema creation or repair."""
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name IN ('derived_indexes','document_cjk_fts')"
            )
        }
        if tables != {"derived_indexes", "document_cjk_fts"}:
            return False
        row = conn.execute(
            "SELECT version FROM derived_indexes WHERE name=?", (CJK_INDEX_NAME,)
        ).fetchone()
        return row is not None and int(row[0]) == CJK_INDEX_VERSION
    except sqlite3.DatabaseError:
        return False


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    meta: dict[str, Any] = {}
    current_list: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if current_list and stripped.startswith("-"):
            meta.setdefault(current_list, []).append(stripped[1:].strip().strip("\"'"))
            continue
        if line[:1].isspace():
            current_list = None
            continue
        current_list = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if not value:
            meta[key] = []
            current_list = key
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key] = [
                part.strip().strip("\"'") for part in inner.split(",") if part.strip()
            ]
        else:
            meta[key] = value.strip("\"'")
    return meta, body


def _derive_metadata(
    path: Path, text: str, root_tags: Iterable[str]
) -> tuple[str, str, tuple[str, ...], str]:
    meta, body = _parse_frontmatter(text)
    title = str(meta.get("title") or "").strip()
    if not title:
        heading = re.search(r"^#\s+(.+?)\s*$", body, flags=re.MULTILINE)
        title = (
            heading.group(1).strip()
            if heading
            else path.stem.replace("-", " ").replace("_", " ")
        )

    brief = str(meta.get("brief") or "").strip()
    if not brief:
        paragraphs = re.split(r"\n\s*\n", body)
        for paragraph in paragraphs:
            cleaned = " ".join(
                line.strip()
                for line in paragraph.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            if cleaned:
                brief = cleaned[:280]
                break
    tags = _dedupe((*root_tags, *_normalize_tags(meta.get("tags"))))
    return title, brief, tags, body


def _tag_search_text(tags: Iterable[str]) -> str:
    """Produce hidden FTS aliases while preserving canonical stored/display tags.

    ``agent-server:learnings`` contributes ``agent-server``, ``agent``, ``server``, and
    ``learnings``. This lets a normal free-text query such as ``learnings agent server``
    recall the note without requiring the user to remember hierarchy order or hyphens.
    """
    aliases: list[str] = []
    for tag in tags:
        canonical = _normalize_tag(tag)
        aliases.append(canonical)
        for segment in canonical.split(":"):
            aliases.append(segment)
            aliases.extend(part for part in re.split(r"[-_\s]+", segment) if part)
    return " ".join(_dedupe(aliases))


def _resolve_link_target(source: Path, href: str) -> tuple[Path | None, str | None]:
    href = href.strip().strip("<>")
    if not href or href.startswith("#"):
        return None, href[1:] if href.startswith("#") else None
    if " " in href and not href.startswith("file://"):
        href = href.split(maxsplit=1)[0]
    parsed = urlparse(href)
    if parsed.scheme and parsed.scheme != "file":
        return None, parsed.fragment or None
    anchor = parsed.fragment or None
    raw_path = unquote(
        parsed.path if parsed.scheme == "file" else href.split("#", 1)[0]
    )
    if not raw_path:
        return None, anchor
    target = Path(raw_path)
    if not target.is_absolute():
        target = source.parent / target
    return target.resolve(strict=False), anchor


def _extract_links(source: Path, body: str) -> list[dict[str, str | None]]:
    found: list[dict[str, str | None]] = []
    for match in MD_LINK_RE.finditer(body):
        label, href = match.group(1).strip(), match.group(2).strip()
        target, anchor = _resolve_link_target(source, href)
        if target is None:
            continue
        found.append(
            {
                "target_path": str(target),
                "href": href,
                "label": label or target.name,
                "anchor": anchor,
            }
        )
    for match in WIKI_LINK_RE.finditer(body):
        raw_target, anchor, alias = (
            match.group(1).strip(),
            match.group(2),
            match.group(3),
        )
        if not Path(raw_target).suffix:
            raw_target += ".md"
        target, _ = _resolve_link_target(source, raw_target)
        if target is None:
            continue
        href = raw_target + (f"#{anchor}" if anchor else "")
        found.append(
            {
                "target_path": str(target),
                "href": href,
                "label": (alias or Path(raw_target).stem).strip(),
                "anchor": anchor,
            }
        )
    unique: dict[tuple[str, str], dict[str, str | None]] = {}
    for item in found:
        unique[(str(item["href"]), str(item["label"]))] = item
    return list(unique.values())


def _is_memory_source(path: Path, root: Path) -> bool:
    """Return whether a Markdown path is a user memory rather than an artifact."""
    folded_stem = path.stem.casefold()
    if (
        path.suffix.casefold() != ".md"
        or path.name.casefold() in MANAGEMENT_FILE_NAMES
        or folded_stem.endswith((".bak", ".backup"))
        or folded_stem.endswith("~")
        or root.name.casefold() in MANAGEMENT_DIRECTORY_NAMES
    ):
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return not any(
        part.casefold() in MANAGEMENT_DIRECTORY_NAMES for part in relative.parts[:-1]
    )


def _iter_memory_sources(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.md"):
        if path.is_file() and _is_memory_source(path, root):
            yield path


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    if isinstance(context, dict):
        return context.get(name, default)
    return getattr(context, name, default)


def _root_parts(value: Any) -> tuple[Path, tuple[str, ...]]:
    if isinstance(value, MemoryRoot):
        return value.path, tuple(value.tags)
    if isinstance(value, dict):
        return Path(value["path"]), _normalize_tags(value.get("tags"))
    path = getattr(value, "path", value)
    tags = getattr(value, "tags", ())
    return Path(path), tuple(tags)


def _enumerate_source_state(
    context: Any, *, global_only: bool = False, include_global: bool = True
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    status = str(_context_value(context, "status", "unavailable"))
    resolved = status == "resolved" and bool(_context_value(context, "project_id"))
    roots: list[tuple[Any, str]] = []
    if include_global:
        roots.extend(
            (root, "global")
            for root in _context_value(context, "global_roots", ()) or ()
        )
    if resolved and not global_only:
        roots.extend(
            (root, "project")
            for root in _context_value(context, "project_roots", ()) or ()
        )

    known = {
        "global": include_global and _context_value(context, "global_roots", None) is not None,
        "project": resolved
        and not global_only
        and _context_value(context, "project_roots", None) is not None,
    }
    aggregated: dict[Path, dict[str, Any]] = {}
    for raw_root, logical_scope in roots:
        try:
            root, root_tags = _root_parts(raw_root)
            root = root.expanduser().resolve(strict=False)
            if not root.is_dir():
                known[logical_scope] = False
                continue
            for path in _iter_memory_sources(root):
                canonical = path.resolve(strict=False)
                item = aggregated.setdefault(
                    canonical,
                    {"path": str(canonical), "scopes": set(), "root_tags": set()},
                )
                item["scopes"].add(logical_scope)
                item["root_tags"].update(root_tags)
        except (OSError, ValueError, TypeError, MemoryError):
            known[logical_scope] = False

    result: list[dict[str, Any]] = []
    for path, item in sorted(aggregated.items(), key=lambda pair: str(pair[0])):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            title, brief, tags, _ = _derive_metadata(path, text, item["root_tags"])
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            for scope in item["scopes"]:
                known[scope] = False
            continue
        result.append(
            {
                "path": item["path"],
                "scopes": sorted(item["scopes"]),
                "tags": list(tags),
                "title": title,
                "brief": brief,
                "mtime_ns": mtime_ns,
            }
        )
    return result, known


def enumerate_sources(context: Any) -> list[dict[str, Any]]:
    """Enumerate visible physical Markdown sources without touching the index."""
    sources, _ = _enumerate_source_state(context)
    return sources


def sync_index(conn: sqlite3.Connection, roots: list[MemoryRoot]) -> dict[str, int]:
    aggregated: dict[Path, dict[str, set[str]]] = {}
    missing_roots = 0
    for root in roots:
        if not root.path.exists():
            missing_roots += 1
            continue
        if not root.path.is_dir():
            raise MemoryError(f"memory root is not a directory: {root.path}")
        for path in _iter_memory_sources(root.path):
            canonical = path.resolve(strict=False)
            entry = aggregated.setdefault(canonical, {"scopes": set(), "tags": set()})
            entry["scopes"].add(root.scope)
            entry["tags"].update(root.tags)

    seen_paths: set[str] = set()
    indexed = 0
    with conn:
        _ensure_cjk_schema(conn)
        if not _cjk_index_is_current(conn):
            conn.execute("DELETE FROM document_cjk_fts")
        for path, visibility in aggregated.items():
            text = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            title, brief, tags, body = _derive_metadata(path, text, visibility["tags"])
            seen_paths.add(str(path))

            existing = conn.execute(
                "SELECT id FROM documents WHERE path=?", (str(path),)
            ).fetchone()
            if existing:
                doc_id = int(existing["id"])
                conn.execute(
                    "UPDATE documents SET title=?, brief=?, mtime_ns=?, size=?, sha256=? WHERE id=?",
                    (title, brief, stat.st_mtime_ns, stat.st_size, digest, doc_id),
                )
                conn.execute("DELETE FROM document_fts WHERE rowid=?", (doc_id,))
                conn.execute("DELETE FROM document_cjk_fts WHERE rowid=?", (doc_id,))
            else:
                cur = conn.execute(
                    "INSERT INTO documents(path,title,brief,mtime_ns,size,sha256) VALUES(?,?,?,?,?,?)",
                    (str(path), title, brief, stat.st_mtime_ns, stat.st_size, digest),
                )
                doc_id = int(cur.lastrowid)

            indexed_content = body + "\n\n" + _tag_search_text(tags)
            conn.execute(
                "INSERT INTO document_fts(rowid,title,brief,content) VALUES(?,?,?,?)",
                (doc_id, title, brief, indexed_content),
            )
            cjk_grams = han_bigrams((title, brief, body))
            if cjk_grams:
                conn.execute(
                    "INSERT INTO document_cjk_fts(rowid,grams) VALUES(?,?)",
                    (doc_id, " ".join(cjk_grams)),
                )
            conn.execute("DELETE FROM document_scopes WHERE document_id=?", (doc_id,))
            conn.executemany(
                "INSERT INTO document_scopes(document_id,scope) VALUES(?,?)",
                [(doc_id, scope) for scope in sorted(visibility["scopes"])],
            )
            conn.execute("DELETE FROM document_tags WHERE document_id=?", (doc_id,))
            conn.executemany(
                "INSERT INTO document_tags(document_id,tag) VALUES(?,?)",
                [(doc_id, tag) for tag in sorted(tags)],
            )
            conn.execute("DELETE FROM links WHERE source_document_id=?", (doc_id,))
            for link in _extract_links(path, body):
                conn.execute(
                    "INSERT OR IGNORE INTO links(source_document_id,target_path,href,label,anchor) VALUES(?,?,?,?,?)",
                    (
                        doc_id,
                        link["target_path"],
                        link["href"],
                        link["label"],
                        link["anchor"],
                    ),
                )
            indexed += 1

        stale = [
            row["id"]
            for row in conn.execute("SELECT id,path FROM documents")
            if row["path"] not in seen_paths
        ]
        for doc_id in stale:
            conn.execute("DELETE FROM document_fts WHERE rowid=?", (doc_id,))
            conn.execute("DELETE FROM document_cjk_fts WHERE rowid=?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

        conn.execute(
            "UPDATE links SET target_document_id=(SELECT id FROM documents d WHERE d.path=links.target_path)"
        )
        conn.execute(
            "INSERT INTO derived_indexes(name,version) VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET version=excluded.version",
            (CJK_INDEX_NAME, CJK_INDEX_VERSION),
        )

    return {
        "indexed": indexed,
        "removed": len(stale),
        "missing_roots": missing_roots,
        "roots": len(roots),
    }


def _fts_terms(text: str) -> list[str]:
    terms = re.findall(r"[\w-]+", text, flags=re.UNICODE)
    if not terms:
        raise MemoryError("search query has no searchable terms")
    return terms


def _fts_query(text: str, operator: str = "AND") -> str:
    return _fts_query_terms(_fts_terms(text), operator)


def _fts_query_terms(terms: Iterable[str], operator: str = "AND") -> str:
    terms = list(terms)
    if operator not in {"AND", "OR"}:
        raise ValueError(f"unsupported FTS operator: {operator}")
    return f" {operator} ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _diagnostic_terms(query: str) -> list[str]:
    """Return original query terms in order, with case-insensitive duplicates removed."""
    seen: set[str] = set()
    result: list[str] = []
    for term in _fts_terms(query):
        folded = term.casefold()
        if folded not in seen:
            seen.add(folded)
            result.append(term)
    return result


def _fallback_terms(terms: Iterable[str]) -> list[str]:
    original = list(terms)
    useful = [
        term
        for term in original
        if not (
            term.isascii()
            and term.isalpha()
            and term.islower()
            and term in NATURAL_LANGUAGE_STOPWORDS
        )
    ]
    return useful or original


def _word_term_matches(conn: sqlite3.Connection, doc_id: int, term: str) -> bool:
    """Ask FTS whether one diagnostic term matched; do not approximate with substrings."""
    return (
        conn.execute(
            "SELECT 1 FROM document_fts WHERE rowid=? AND document_fts MATCH ?",
            (doc_id, _fts_query_terms([term])),
        ).fetchone()
        is not None
    )


def _add_word_diagnostics(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    terms: list[str],
    mode: str,
    route_terms: Iterable[str] | None = None,
) -> dict[str, Any]:
    if mode == "strict":
        matched = list(terms)
    else:
        eligible = {
            term.casefold() for term in (route_terms if route_terms is not None else terms)
        }
        matched = [
            term
            for term in terms
            if term.casefold() in eligible
            and _word_term_matches(conn, payload["id"], term)
        ]
    matched_folded = {term.casefold() for term in matched}
    payload.update(
        {
            "match_mode": mode,
            "matched_terms": matched,
            "missing_terms": [
                term for term in terms if term.casefold() not in matched_folded
            ],
            "retrieval_routes": ["word"],
        }
    )
    return payload


def _append_tag_filter(sql: str, params: list[Any], tag: str) -> str:
    """Match hierarchy segments without requiring the stored hierarchy order.

    ``agent-server:learnings`` can therefore be filtered by ``learnings``,
    ``agent-server:learnings``, or ``learnings:agent-server``. Stored/displayed tags stay
    canonical, and every requested segment must match a complete colon-delimited segment.
    """
    canonical = _normalize_tag(tag)
    segments = canonical.split(":")
    conditions = [
        "instr(':' || lower(t.tag) || ':', ':' || lower(?) || ':') > 0"
        for _ in segments
    ]
    sql += (
        " AND EXISTS (SELECT 1 FROM document_tags t WHERE t.document_id=d.id AND "
        + " AND ".join(conditions)
        + ")"
    )
    params.extend(segments)
    return sql


def _doc_payload(
    conn: sqlite3.Connection, row: sqlite3.Row, score: float | None = None
) -> dict[str, Any]:
    doc_id = int(row["id"])
    payload: dict[str, Any] = {
        "id": doc_id,
        "title": row["title"],
        "brief": row["brief"],
        "path": row["path"],
        "projects": [
            r[0]
            for r in conn.execute(
                "SELECT scope FROM document_scopes WHERE document_id=? ORDER BY scope",
                (doc_id,),
            )
        ],
        "tags": [
            r[0]
            for r in conn.execute(
                "SELECT tag FROM document_tags WHERE document_id=? ORDER BY tag",
                (doc_id,),
            )
        ],
    }
    if score is not None:
        payload["score"] = score
    return payload


def _scope_and_tag_filters(
    sql: str,
    params: list[Any],
    project: str | None,
    tags: tuple[str, ...],
    include_shared: bool,
) -> str:
    if project:
        scopes = [project] + ([SHARED_SCOPE] if include_shared else [])
        placeholders = ",".join("?" for _ in scopes)
        sql += (
            " AND EXISTS (SELECT 1 FROM document_scopes s "
            f"WHERE s.document_id=d.id AND s.scope IN ({placeholders}))"
        )
        params.extend(scopes)
    for tag in tags:
        sql = _append_tag_filter(sql, params, tag)
    return sql


def _original_indexed_fields(
    conn: sqlite3.Connection, row: sqlite3.Row
) -> tuple[str, str, str]:
    tag_text = _tag_search_text(
        r[0]
        for r in conn.execute(
            "SELECT tag FROM document_tags WHERE document_id=? ORDER BY tag",
            (int(row["id"]),),
        )
    )
    suffix = "\n\n" + tag_text
    content = str(row["indexed_content"])
    body = content[: -len(suffix)] if content.endswith(suffix) else content
    return str(row["title"]), str(row["brief"]), body


def _cjk_term_matches(conn: sqlite3.Connection, row: sqlite3.Row, term: str) -> bool:
    return any(term in field for field in _original_indexed_fields(conn, row))


def _cjk_candidates(
    conn: sqlite3.Connection,
    grams: list[str],
    runs: list[str],
    project: str | None,
    tags: tuple[str, ...],
    include_shared: bool,
    candidate_limit: int,
) -> list[dict[str, Any]]:
    if not grams or not _cjk_index_is_current(conn):
        return []

    def execute(operator: str, result_limit: int | None) -> list[sqlite3.Row]:
        sql = """
            SELECT d.id,d.path,d.title,d.brief,
                   document_fts.content AS indexed_content,
                   document_cjk_fts.grams AS indexed_grams,
                   bm25(document_cjk_fts) AS score
            FROM document_cjk_fts
            JOIN documents d ON d.id=document_cjk_fts.rowid
            JOIN document_fts ON document_fts.rowid=d.id
            WHERE document_cjk_fts MATCH ?
        """
        params: list[Any] = [_fts_query_terms(grams, operator)]
        sql = _scope_and_tag_filters(sql, params, project, tags, include_shared)
        sql += " ORDER BY score,d.path"
        if result_limit is not None:
            sql += " LIMIT ?"
            params.append(result_limit)
        return list(conn.execute(sql, params))

    full_hits: list[dict[str, Any]] = []
    for row in execute("AND", candidate_limit):
        if all(_cjk_term_matches(conn, row, run) for run in runs):
            full_hits.append(
                {
                    "row": row,
                    "score": float(row["score"]),
                    "matched_grams": list(grams),
                    "coverage": 1.0,
                    "full": True,
                }
            )
    if full_hits:
        return full_hits

    # A two-character query has one gram and is admitted only through the full
    # contiguous check above.  Longer queries may use the explicitly relaxed route.
    if len(grams) == 1:
        return []
    query_grams = set(grams)
    relaxed: list[dict[str, Any]] = []
    for row in execute("OR", None):
        matched = [gram for gram in grams if gram in set(row["indexed_grams"].split())]
        if len(matched) < 2 or len(matched) * 2 < len(grams):
            continue
        relaxed.append(
            {
                "row": row,
                "score": float(row["score"]),
                "matched_grams": matched,
                "coverage": len(set(matched) & query_grams) / len(query_grams),
                "full": False,
            }
        )
    relaxed.sort(
        key=lambda item: (
            -len(item["matched_grams"]),
            item["score"],
            item["row"]["path"],
        )
    )
    return relaxed[:candidate_limit]


def _fused_payload(
    conn: sqlite3.Connection,
    query_terms: list[str],
    word: dict[str, Any] | None,
    cjk: dict[str, Any] | None,
    rank_score: float,
) -> dict[str, Any]:
    chosen = word or cjk
    assert chosen is not None
    row = chosen["row"]
    score = float(word["score"] if word is not None else cjk["score"])
    payload = _doc_payload(conn, row, score)

    matched: list[str] = []
    word_mode = word["mode"] if word is not None else None
    for term in query_terms:
        word_match = word is not None and (
            word_mode == "strict"
            or (
                term.casefold() in word["route_terms"]
                and _word_term_matches(conn, payload["id"], term)
            )
        )
        cjk_match = (
            cjk is not None
            and bool(han_runs(term))
            and _cjk_term_matches(conn, row, term)
        )
        if word_match or cjk_match:
            matched.append(term)
    matched_folded = {term.casefold() for term in matched}

    routes = (["word"] if word is not None else []) + (
        ["cjk"] if cjk is not None else []
    )
    if cjk is not None and not cjk["full"]:
        mode = "relaxed"
    elif word is not None and cjk is not None:
        mode = "hybrid"
    elif cjk is not None:
        mode = "cjk"
    else:
        mode = str(word_mode)
    payload.update(
        {
            "match_mode": mode,
            "matched_terms": matched,
            "missing_terms": [
                term for term in query_terms if term.casefold() not in matched_folded
            ],
            "retrieval_routes": routes,
            "rank_score": rank_score,
        }
    )
    if cjk is not None:
        payload["matched_grams"] = list(cjk["matched_grams"])
        payload["gram_coverage"] = float(cjk["coverage"])
    return payload


def search_documents(
    conn: sqlite3.Connection,
    query: str,
    project: str | None = None,
    tags: Iterable[str] = (),
    limit: int = 10,
    include_shared: bool = True,
) -> list[dict[str, Any]]:
    tags = tuple(tags)
    diagnostic_terms = _diagnostic_terms(query)

    def execute(
        match_query: str, result_limit: int, stable_ties: bool = False
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT d.id,d.path,d.title,d.brief,document_fts.content AS indexed_content,
                   bm25(document_fts) AS score
            FROM document_fts JOIN documents d ON d.id=document_fts.rowid
            WHERE document_fts MATCH ?
        """
        params: list[Any] = [match_query]
        sql = _scope_and_tag_filters(sql, params, project, tags, include_shared)
        sql += " ORDER BY score" + (",d.path" if stable_ties else "") + " LIMIT ?"
        params.append(result_limit)
        return list(conn.execute(sql, params))

    query_runs = han_runs(query)
    if not query_runs:
        strict_hits = execute(_fts_query(query), limit)
    else:
        strict_hits = []
    if strict_hits:
        return [
            _add_word_diagnostics(
                conn,
                _doc_payload(conn, row, float(row["score"])),
                diagnostic_terms,
                "strict",
            )
            for row in strict_hits
        ]

    # Natural-language recall often contains an extra symptom or synonym that is
    # absent from a concise memory. Preserve precise AND results when they exist,
    # but make a no-result query useful by recalling documents that match any term.
    # Deduplicate and remove only a small set of plain-language stopwords, then let
    # equal-weight FTS5 BM25 rank every indexed field.  If all terms are stopwords,
    # retain the original terms so the query remains searchable.
    fallback_terms = _fallback_terms(diagnostic_terms)
    if query_runs:
        candidate_limit = max(limit * 20, 100)
        word_hits = execute(_fts_query(query), candidate_limit, True)
        word_mode = "strict"
        if not word_hits:
            word_hits = execute(
                _fts_query_terms(fallback_terms, "OR"), candidate_limit, True
            )
            word_mode = "relaxed"

        query_grams = han_bigrams((query,))
        cjk_hits = _cjk_candidates(
            conn,
            query_grams,
            query_runs,
            project,
            tags,
            include_shared,
            candidate_limit,
        )
        if not _cjk_index_is_current(conn):
            # Old/read-only indexes retain their established word-only behavior.
            if word_mode == "relaxed":
                folded_terms = [term.casefold() for term in diagnostic_terms]
                word_hits.sort(
                    key=lambda row: (
                        -sum(
                            term in row["indexed_content"].casefold()
                            for term in folded_terms
                        ),
                        float(row["score"]),
                    )
                )
            return [
                _add_word_diagnostics(
                    conn,
                    _doc_payload(conn, row, float(row["score"])),
                    diagnostic_terms,
                    word_mode,
                    fallback_terms if word_mode == "relaxed" else diagnostic_terms,
                )
                for row in word_hits[:limit]
            ]

        word_ranked = {
            int(row["id"]): {
                "row": row,
                "score": float(row["score"]),
                "mode": word_mode,
                "route_terms": {
                    term.casefold()
                    for term in (
                        fallback_terms if word_mode == "relaxed" else diagnostic_terms
                    )
                },
                "rank": rank,
            }
            for rank, row in enumerate(word_hits, 1)
        }
        cjk_ranked = {
            int(item["row"]["id"]): {**item, "rank": rank}
            for rank, item in enumerate(cjk_hits, 1)
        }
        fused: list[tuple[float, str, int]] = []
        for doc_id in word_ranked.keys() | cjk_ranked.keys():
            rank_score = 0.0
            if doc_id in word_ranked:
                rank_score += 1.0 / (60 + word_ranked[doc_id]["rank"])
            if doc_id in cjk_ranked:
                rank_score += 1.0 / (60 + cjk_ranked[doc_id]["rank"])
            source = word_ranked.get(doc_id) or cjk_ranked[doc_id]
            fused.append((rank_score, str(source["row"]["path"]), doc_id))
        fused.sort(key=lambda item: (-item[0], item[1]))
        return [
            _fused_payload(
                conn,
                diagnostic_terms,
                word_ranked.get(doc_id),
                cjk_ranked.get(doc_id),
                rank_score,
            )
            for rank_score, _, doc_id in fused[:limit]
        ]

    fallback_hits = execute(
        _fts_query_terms(fallback_terms, "OR"), max(limit * 20, 100)
    )
    return [
        _add_word_diagnostics(
            conn,
            _doc_payload(conn, row, float(row["score"])),
            diagnostic_terms,
            "relaxed",
            fallback_terms,
        )
        for row in fallback_hits[:limit]
    ]


def list_documents(
    conn: sqlite3.Connection,
    project: str | None = None,
    tags: Iterable[str] = (),
    limit: int = 100,
    include_shared: bool = True,
) -> list[dict[str, Any]]:
    sql = "SELECT d.id,d.path,d.title,d.brief FROM documents d WHERE 1=1"
    params: list[Any] = []
    if project:
        scopes = [project] + ([SHARED_SCOPE] if include_shared else [])
        placeholders = ",".join("?" for _ in scopes)
        sql += f" AND EXISTS (SELECT 1 FROM document_scopes s WHERE s.document_id=d.id AND s.scope IN ({placeholders}))"
        params.extend(scopes)
    for tag in tags:
        sql = _append_tag_filter(sql, params, tag)
    sql += " ORDER BY d.mtime_ns DESC,d.path LIMIT ?"
    params.append(limit)
    return [_doc_payload(conn, row) for row in conn.execute(sql, params)]


def _inventory_scopes(
    context: Any, filters: dict[str, Any]
) -> tuple[list[str], str | None, bool]:
    project = _context_value(context, "project_id")
    resolved = (
        str(_context_value(context, "status", "unavailable")) == "resolved"
        and bool(project)
    )
    global_only = bool(
        filters.get("global", False) or filters.get("global_only", False)
    )
    include_global = bool(
        filters.get("include_global", filters.get("include_shared", True))
    )
    scopes: list[str] = []
    if include_global:
        scopes.append(SHARED_SCOPE)
    if resolved and not global_only:
        scopes.append(str(project))
    return scopes, str(project) if resolved and not global_only else None, global_only


def _tag_filter_matches(tags: Iterable[str], requested: Iterable[str]) -> bool:
    available = [":" + tag.casefold() + ":" for tag in tags]
    for raw in requested:
        segments = _normalize_tag(raw).casefold().split(":")
        if not any(all(f":{segment}:" in tag for segment in segments) for tag in available):
            return False
    return True


def _requested_tags(filters: dict[str, Any]) -> tuple[str, ...]:
    value = filters.get("tags", ()) or ()
    return (value,) if isinstance(value, str) else tuple(value)


def _unknown_inventory() -> dict[str, Any]:
    return {
        "counts": {"distinct": None, "global": None, "project": None},
        "tags": [],
        "route_candidates": [],
    }


def _navigation_inventory_db(
    conn: sqlite3.Connection, context: Any, filters: dict[str, Any]
) -> dict[str, Any]:
    scopes, project, global_only = _inventory_scopes(context, filters)
    if not scopes:
        return _unknown_inventory()
    placeholders = ",".join("?" for _ in scopes)
    visible_sql = (
        "SELECT DISTINCT d.id FROM documents d JOIN document_scopes vs "
        f"ON vs.document_id=d.id WHERE vs.scope IN ({placeholders})"
    )
    visible_params: list[Any] = list(scopes)
    for tag in _requested_tags(filters):
        visible_sql = _append_tag_filter(visible_sql, visible_params, tag)
    visible_ids = [int(row[0]) for row in conn.execute(visible_sql, visible_params)]
    visible_set = set(visible_ids)

    def scope_count(scope: str) -> int:
        return int(
            conn.execute(
                "SELECT count(DISTINCT document_id) FROM document_scopes "
                "WHERE scope=? AND document_id IN (" + visible_sql + ")",
                [scope, *visible_params],
            ).fetchone()[0]
        )

    global_count = scope_count(SHARED_SCOPE) if SHARED_SCOPE in scopes else 0
    status_resolved = str(_context_value(context, "status", "unavailable")) == "resolved"
    project_count: int | None
    if global_only:
        project_count = 0
    elif project is not None:
        project_count = scope_count(project)
    elif status_resolved:
        project_count = 0
    else:
        project_count = None

    tags: list[dict[str, Any]] = []
    if visible_ids:
        ids_sql = ",".join("?" for _ in visible_ids)
        tags = [
            {"tag": str(row[0]), "count": int(row[1])}
            for row in conn.execute(
                "SELECT tag,count(DISTINCT document_id) AS n FROM document_tags "
                f"WHERE document_id IN ({ids_sql}) GROUP BY tag "
                "ORDER BY n DESC,lower(tag),tag LIMIT 5",
                visible_ids,
            )
        ]

    candidate_sql = "SELECT d.id,d.path,d.title,d.brief FROM documents d WHERE 1=1"
    candidate_params: list[Any] = []
    if visible_ids:
        candidate_sql += " AND d.id IN (" + ",".join("?" for _ in visible_ids) + ")"
        candidate_params.extend(visible_ids)
    else:
        candidate_sql += " AND 0"
    candidate_sql += " ORDER BY d.mtime_ns DESC,d.path LIMIT ?"
    try:
        limit = max(0, min(int(filters.get("limit", 6)), 20))
    except (TypeError, ValueError):
        limit = 6
    candidate_params.append(limit)
    candidates = []
    for row in conn.execute(candidate_sql, candidate_params):
        payload = _doc_payload(conn, row)
        payload["projects"] = [scope for scope in payload["projects"] if scope in scopes]
        candidates.append(payload)
    return {
        "counts": {
            "distinct": len(visible_set),
            "global": global_count,
            "project": project_count,
        },
        "tags": tags,
        "route_candidates": candidates,
    }


def navigation_inventory(context: Any, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a bounded, scope-safe inventory separate from search results.

    The function is deliberately SELECT-only.  If a caller supplies a read-only
    SQLite connection as ``context.connection`` (or ``context.conn``), it uses
    indexed metadata.  Otherwise it derives the same navigation facts in memory
    from the context roots, so a missing cache does not make memory undiscoverable.
    """
    filters = dict(filters or {})
    conn = _context_value(context, "connection", _context_value(context, "conn"))
    if conn is not None:
        try:
            return _navigation_inventory_db(conn, context, filters)
        except (sqlite3.Error, KeyError, TypeError, ValueError, MemoryError):
            return _unknown_inventory()

    _, project, global_only = _inventory_scopes(context, filters)
    include_global = bool(
        filters.get("include_global", filters.get("include_shared", True))
    )
    sources, known = _enumerate_source_state(
        context, global_only=global_only, include_global=include_global
    )
    if not sources and not any(known.values()):
        return _unknown_inventory()
    visible = [
        item
        for item in sources
        if (include_global and "global" in item["scopes"])
        or (project is not None and "project" in item["scopes"])
    ]
    requested_tags = _requested_tags(filters)
    filtered = [
        item for item in visible if _tag_filter_matches(item["tags"], requested_tags)
    ]
    global_count = (
        0
        if not include_global
        else sum("global" in item["scopes"] for item in filtered)
        if known["global"]
        else None
    )
    if global_only:
        project_count: int | None = 0
    elif project is None:
        project_count = None
    else:
        project_count = (
            sum("project" in item["scopes"] for item in filtered)
            if known["project"]
            else None
        )
    required_scopes = (["global"] if include_global else []) + (
        ["project"] if project else []
    )
    distinct = len(filtered) if all(known[scope] for scope in required_scopes) else None
    counts: dict[str, int] = {}
    for item in filtered:
        for tag in set(item["tags"]):
            counts[tag] = counts.get(tag, 0) + 1
    tags = [
        {"tag": tag, "count": count}
        for tag, count in sorted(
            counts.items(), key=lambda pair: (-pair[1], pair[0].casefold(), pair[0])
        )[:5]
    ]
    candidates = list(filtered)
    candidates.sort(key=lambda item: (-item["mtime_ns"], item["path"]))
    try:
        limit = max(0, min(int(filters.get("limit", 6)), 20))
    except (TypeError, ValueError):
        limit = 6
    for item in candidates:
        item.pop("mtime_ns", None)
        item.pop("root_tags", None)
    return {
        "counts": {
            "distinct": distinct,
            "global": global_count,
            "project": project_count,
        },
        "tags": tags,
        "route_candidates": candidates[:limit],
    }


def resolve_document(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    if ref.isdigit():
        row = conn.execute(
            "SELECT id,path,title,brief FROM documents WHERE id=?", (int(ref),)
        ).fetchone()
        if row:
            return row
    candidate = _expand_path(ref)
    row = conn.execute(
        "SELECT id,path,title,brief FROM documents WHERE path=?", (str(candidate),)
    ).fetchone()
    if row:
        return row
    suffix = ref.replace("\\", "/").lstrip("./")
    matches = conn.execute(
        "SELECT id,path,title,brief FROM documents WHERE replace(path,'\\','/') LIKE ?",
        (f"%/{suffix}",),
    ).fetchall()
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise MemoryError(f"document reference is ambiguous: {ref}")
    raise MemoryError(f"document not found in index: {ref}")


def link_graph(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    doc = resolve_document(conn, ref)
    doc_id = int(doc["id"])
    outbound = []
    for row in conn.execute(
        """SELECT l.href,l.label,l.anchor,l.target_path,l.target_document_id,d.title AS target_title
           FROM links l LEFT JOIN documents d ON d.id=l.target_document_id
           WHERE l.source_document_id=? ORDER BY l.id""",
        (doc_id,),
    ):
        outbound.append(
            {
                "label": row["label"],
                "href": row["href"],
                "anchor": row["anchor"],
                "path": row["target_path"],
                "title": row["target_title"],
                "resolved": row["target_document_id"] is not None,
            }
        )
    inbound = []
    for row in conn.execute(
        """SELECT l.href,l.label,l.anchor,s.id AS source_id,s.path AS source_path,s.title AS source_title
           FROM links l JOIN documents s ON s.id=l.source_document_id
           WHERE l.target_document_id=? ORDER BY s.path,l.id""",
        (doc_id,),
    ):
        inbound.append(
            {
                "id": row["source_id"],
                "title": row["source_title"],
                "path": row["source_path"],
                "label": row["label"],
                "href": row["href"],
                "anchor": row["anchor"],
            }
        )
    return {
        "document": _doc_payload(conn, doc),
        "outbound": outbound,
        "inbound": inbound,
    }


def status(
    conn: sqlite3.Connection, settings_path: Path, db_path: Path
) -> dict[str, Any]:
    return {
        "settings": str(settings_path),
        "database": str(db_path),
        "documents": conn.execute("SELECT count(*) FROM documents").fetchone()[0],
        "links": conn.execute("SELECT count(*) FROM links").fetchone()[0],
        "resolved_links": conn.execute(
            "SELECT count(*) FROM links WHERE target_document_id IS NOT NULL"
        ).fetchone()[0],
    }
