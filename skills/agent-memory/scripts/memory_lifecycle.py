"""Explicit lifecycle transitions and stable-ID migration for Markdown memory sources."""

from __future__ import annotations
from pathlib import Path
from memory_config import MemoryError
from memory_capture import LIFECYCLE_STATES, new_memory_id
from memory_store_ext import resolve_document


def _set_fields(path: Path, fields: dict[str, str | None], create_frontmatter=False):
    text = path.read_bytes().decode("utf-8")
    first_newline = text.find("\n")
    opening = text[:first_newline].removesuffix("\r") if first_newline >= 0 else text
    if opening != "---":
        if not create_frontmatter:
            raise MemoryError(f"lifecycle update requires frontmatter: {path}")
        prefix = [f"{k}: {v}" for k, v in fields.items() if v is not None]
        path.write_bytes(("---\n" + "\n".join(prefix) + "\n---\n\n" + text).encode("utf-8"))
        return
    if first_newline < 0:
        raise MemoryError(f"unterminated frontmatter: {path}")
    front_start = first_newline + 1
    line_ending = "\r\n" if text[first_newline - 1 : first_newline] == "\r" else "\n"
    cursor = front_start
    close_start = close_end = None
    while cursor <= len(text):
        newline = text.find("\n", cursor)
        next_cursor = len(text) if newline < 0 else newline + 1
        line = text[cursor:next_cursor]
        value = line.rstrip("\r\n")
        if value == "---":
            close_start, close_end = cursor, next_cursor
            break
        if next_cursor == cursor or newline < 0:
            break
        cursor = next_cursor
    if close_start is None or close_end is None:
        raise MemoryError(f"unterminated frontmatter: {path}")
    keys = set(fields)
    out = []
    seen = set()
    cursor = front_start
    while cursor < close_start:
        newline = text.find("\n", cursor, close_start)
        next_cursor = close_start if newline < 0 else newline + 1
        line = text[cursor:next_cursor]
        content = line.rstrip("\r\n")
        key = (
            content.split(":", 1)[0].strip()
            if content and not content[0].isspace() and ":" in content
            else ""
        )
        if key in keys:
            seen.add(key)
            value = fields[key]
            if value is not None:
                ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                out.append(f"{key}: {value}{ending}")
        else:
            out.append(line)
        cursor = next_cursor
    if out and not out[-1].endswith(("\n", "\r")) and any(
        key not in seen and value is not None for key, value in fields.items()
    ):
        out.append(line_ending)
    for key, value in fields.items():
        if key not in seen and value is not None:
            out.append(f"{key}: {value}{line_ending}")
    path.write_bytes((text[:front_start] + "".join(out) + text[close_start:]).encode("utf-8"))


def update_lifecycle(conn, ref, status, *, target=None):
    status = status.strip().lower()
    if status not in LIFECYCLE_STATES:
        raise MemoryError(f"unsupported lifecycle status: {status}")
    doc = resolve_document(conn, ref)
    path = Path(doc["path"])
    fields = {"status": status, "promoted_to": None, "superseded_by": None}
    if status == "promoted":
        if not target:
            raise MemoryError(
                "promoted lifecycle requires --target memory ID/reference"
            )
        fields["promoted_to"] = (
            target if target.startswith("memory://") else f"memory://{target}"
        )
    elif status == "superseded":
        if not target:
            raise MemoryError(
                "superseded lifecycle requires --target memory ID/reference"
            )
        fields["superseded_by"] = (
            target if target.startswith("memory://") else f"memory://{target}"
        )
    _set_fields(path, fields)
    return {"path": str(path), "status": status, "target": target}


def update_tier(conn, ref, tier):
    tier = str(tier).strip().lower()
    if tier not in {"core", "archive"}:
        raise MemoryError(f"unsupported tier: {tier}")
    doc = resolve_document(conn, ref)
    path = Path(doc["path"])
    _set_fields(path, {"tier": tier}, create_frontmatter=True)
    return {"path": str(path), "tier": tier}


def migrate_ids(conn):
    changed = []
    for row in conn.execute(
        "SELECT d.path FROM documents d LEFT JOIN memory_meta m ON m.document_id=d.id WHERE m.memory_id IS NULL OR m.memory_id='' "
    ).fetchall():
        path = Path(row[0])
        mid = new_memory_id()
        _set_fields(path, {"id": mid}, create_frontmatter=True)
        changed.append({"path": str(path), "memory_id": mid})
    return {"migrated": len(changed), "documents": changed}
