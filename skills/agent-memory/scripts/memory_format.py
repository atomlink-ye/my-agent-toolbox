"""Deterministic stdlib-only output renderers for the Agent Memory CLI.

The YAML writer intentionally emits a small JSON-compatible YAML subset.  JSON
quoted strings, JSON booleans/null, and simple block collections are understood
by both YAML 1.1 and YAML 1.2 parsers without adding a runtime dependency.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable

OUTPUT_BUDGET = 2048
CONTEXT_OUTPUT_BUDGET = 8192

_PLAIN_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
_YAML_BOOL_OR_NULL = {"null", "true", "false", "yes", "no", "on", "off", "y", "n", "~"}
_YAML_NUMBER_RE = re.compile(
    r"[-+]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?\Z"
)


def _yaml_key(value: object) -> str:
    """Render a mapping key while avoiding YAML's implicit key typing."""
    text = str(value)
    lowered = text.casefold()
    if (
        _PLAIN_KEY_RE.fullmatch(text)
        and lowered not in _YAML_BOOL_OR_NULL
        and not _YAML_NUMBER_RE.fullmatch(text)
    ):
        return text
    return json.dumps(text, ensure_ascii=False)


def _yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ".nan"
        if math.isinf(value):
            return ".inf" if value > 0 else "-.inf"
        return str(value)
    # JSON strings are valid YAML double-quoted scalars. Always quoting strings
    # avoids YAML's implicit typing (for example, a title of "yes" stays a string).
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"unsupported YAML scalar: {type(value).__name__}")


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _yaml_mapping_item(
    key: object, value: object, indent: int, prefix: str = ""
) -> list[str]:
    """Render one mapping item, including nested collections."""
    key_text = _yaml_key(key)
    if _is_scalar(value):
        return [f"{prefix}{key_text}: {_yaml_scalar(value)}"]
    if isinstance(value, dict) and not value:
        return [f"{prefix}{key_text}: {{}}"]
    if isinstance(value, list) and not value:
        return [f"{prefix}{key_text}: []"]
    return [f"{prefix}{key_text}:", yaml_dump(value, indent + 2)]


def yaml_dump(value: object, indent: int = 0) -> str:
    """Serialize JSON-shaped data to a deterministic, parseable YAML subset."""
    prefix = " " * indent
    if _is_scalar(value):
        return prefix + _yaml_scalar(value)
    if isinstance(value, dict):
        if not value:
            return prefix + "{}"
        lines: list[str] = []
        for key, item in value.items():
            lines.extend(_yaml_mapping_item(key, item, indent, prefix))
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return prefix + "[]"
        lines: list[str] = []
        for item in value:
            if _is_scalar(item):
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
            elif isinstance(item, dict):
                if not item:
                    lines.append(f"{prefix}- {{}}")
                    continue
                first, *rest = item.items()
                lines.extend(
                    _yaml_mapping_item(first[0], first[1], indent + 2, f"{prefix}- ")
                )
                for key, child in rest:
                    lines.extend(
                        _yaml_mapping_item(key, child, indent + 2, f"{prefix}  ")
                    )
            elif isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(yaml_dump(item, indent + 2))
            else:
                raise TypeError(f"unsupported YAML value: {type(item).__name__}")
        return "\n".join(lines)
    raise TypeError(f"unsupported YAML value: {type(value).__name__}")


def _compact_path_base(result: list[object], memory_roots: Iterable[Path]) -> Path | None:
    """Return one base that makes every displayed document path directly resolvable."""
    paths = [
        Path(item["path"]).expanduser().resolve(strict=False)
        for item in result
        if isinstance(item, dict) and item.get("path")
    ]
    roots = [Path(root).expanduser().resolve(strict=False) for root in memory_roots]
    used_roots = [
        root
        for root in roots
        if any(path == root or root in path.parents for path in paths)
    ]
    if not paths or not used_roots:
        return None
    try:
        return Path(os.path.commonpath([str(root) for root in used_roots]))
    except ValueError:
        return None


def compact_dump(result: object, memory_roots: Iterable[Path] = ()) -> str:
    """Render search/list rows as routing hints instead of diagnostic records."""
    if not isinstance(result, list):
        raise TypeError("compact output requires a list result")
    if not result:
        return "(no results)"

    base = _compact_path_base(result, memory_roots)
    lines = [f"Read paths relative to {base}:"] if base is not None else []
    for index, item in enumerate(result, 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "(untitled)").strip()
        match_mode = str(item.get("match_mode") or "strict").strip()
        suffix = "" if match_mode == "strict" else f" ~{match_mode}"
        lines.append(f"{index}. {title}{suffix}")

        brief = str(item.get("brief") or "").strip()
        if brief and brief != title:
            lines.append(f"   {brief}")

        raw_path = Path(str(item.get("path") or "")).expanduser()
        display_path = raw_path
        if base is not None:
            try:
                display_path = raw_path.resolve(strict=False).relative_to(base)
            except ValueError:
                pass
        lines.append(f"   {display_path}")
    return "\n".join(lines)


def _safe_text(value: object) -> str:
    """Keep navigation output single-line and harmless to terminals."""
    return " ".join(str(value).replace("\x1b", "?").split())


def navigation_text(payload: dict[str, Any]) -> str:
    """Render an opening/no-match packet as terse, readable routing data."""
    scope = payload.get("scope", {})
    project = scope.get("project") or "unknown"
    parts = [f"status={_safe_text(payload.get('status', 'unknown'))}"]
    if payload.get("query") is not None:
        parts.append(f"match_mode={_safe_text(payload.get('match_mode', 'none'))}")
    parts.extend(
        [
            f"project={_safe_text(project)}",
            f"source={_safe_text(scope.get('source', 'unknown'))}",
        ]
    )
    lines = [" ".join(parts)]
    counts = payload.get("counts", {})
    lines.append(
        "counts: distinct={} global={} project={} core={} archive={} defaulted={} (components may overlap)".format(
            counts.get("distinct", "unknown"),
            counts.get("global", "unknown"),
            counts.get("project", "unknown") if counts.get("project") is not None else "unknown",
            counts.get("core", "unknown"),
            counts.get("archive", "unknown"),
            counts.get("defaulted", "unknown"),
        )
    )
    tags = payload.get("tags", [])
    if tags:
        lines.append(
            "tags: " + ", ".join(f"{_safe_text(item['tag'])}({item['count']})" for item in tags)
        )
    candidates = payload.get("configured_projects", [])
    if candidates:
        lines.append("configured_projects: " + ", ".join(_safe_text(x) for x in candidates[:3]))
    if payload.get("diagnostic"):
        lines.append("diagnostic: " + _safe_text(payload["diagnostic"]))
    diagnostics = payload.get("diagnostics", [])
    if diagnostics:
        codes = []
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            code = _safe_text(item.get("code", "unknown"))
            if item.get("count") is not None:
                code += f"({item['count']})"
            codes.append(code)
        if codes:
            lines.append("diagnostics: " + ", ".join(codes))
    if payload.get("tier_guidance"):
        lines.append("guidance: " + _safe_text(payload["tier_guidance"]))
    rows = payload.get("navigation", [])
    if rows:
        lines.append("navigation:")
        for row in rows:
            lines.append(f"- [{_safe_text(row.get('scope', ''))}] {_safe_text(row.get('title', ''))}")
            lines.append(f"  {_safe_text(row.get('path', ''))}")
            if row.get("brief"):
                lines.append(f"  brief: {_safe_text(row['brief'])}")
    summaries = payload.get("project_summaries", [])
    if summaries:
        lines.append("other projects:")
        lines.extend(
            f"- {_safe_text(item.get('project', 'unknown'))} ({item.get('count', 0)})"
            for item in summaries
            if isinstance(item, dict)
        )
    lines.append(f"omitted: {payload.get('omitted', 0)}")
    lines.append("note: navigation, not search hits")
    commands = payload.get("next_commands", [])
    next_step = (
        "; then ".join(commands)
        if payload.get("tier_guidance")
        else (commands[0] if commands else "agent-memory brief")
    )
    lines.append("next: " + _safe_text(next_step))
    if payload.get("truncated"):
        lines.append("truncated: true")
    return "\n".join(lines)


def _recount_omitted(payload: dict[str, Any]) -> None:
    distinct = payload.get("counts", {}).get("distinct")
    if isinstance(distinct, int):
        shown = {row.get("id", row.get("path")) for row in payload.get("navigation", [])}
        payload["omitted"] = max(0, distinct - len(shown))


def _prune_navigation_brief(payload: dict[str, Any]) -> bool:
    """Shorten one selected brief before dropping its navigation candidate."""
    rows = [
        row
        for row in payload.get("navigation", [])
        if isinstance(row, dict) and isinstance(row.get("brief"), str) and row["brief"]
    ]
    if not rows:
        return False
    row = max(rows, key=lambda item: len(item["brief"].encode("utf-8")))
    brief = row["brief"]
    if len(brief) > 64:
        row["brief"] = brief[: max(32, len(brief) // 2)].rstrip() + "…"
    else:
        row.pop("brief", None)
    return True


def _shorten_utf8(value: object, budget: int) -> str:
    suffix = "…"
    prefix_budget = max(0, budget - len(suffix.encode("utf-8")))
    prefix = str(value).encode("utf-8")[:prefix_budget].decode("utf-8", "ignore")
    return prefix + suffix


def _prune_navigation_payload(payload: dict[str, Any]) -> bool:
    if payload.get("tags"):
        payload["tags"].pop()
    elif payload.get("project_summaries"):
        payload["project_summaries"].pop()
    elif _prune_navigation_brief(payload):
        pass
    elif len(payload.get("navigation", [])) > 1:
        payload["navigation"].pop()
        _recount_omitted(payload)
    elif len(str(payload.get("query", "")).encode("utf-8")) > 32:
        payload["query"] = _shorten_utf8(payload["query"], 32)
    elif payload.get("navigation"):
        payload["navigation"].pop()
        _recount_omitted(payload)
    elif payload.get("configured_projects"):
        payload["configured_projects"].pop()
    elif len(str(payload.get("diagnostic", "")).encode("utf-8")) > 160:
        payload["diagnostic"] = _shorten_utf8(payload["diagnostic"], 160)
    elif payload.get("diagnostic"):
        payload["diagnostic"] = None
    elif len(str(payload.get("tier_guidance", "")).encode("utf-8")) > 160:
        payload["tier_guidance"] = _shorten_utf8(payload["tier_guidance"], 160)
    elif payload.get("tier_guidance"):
        payload["tier_guidance"] = None
    elif payload.get("diagnostics"):
        payload["diagnostics"].pop()
    elif any(
        len(str(command).encode("utf-8")) > 256
        for command in payload.get("next_commands", [])
    ):
        compacted = []
        for command in payload.get("next_commands", []):
            command = str(command)
            if len(command.encode("utf-8")) > 256:
                if command.startswith("agent-memory context"):
                    command = "agent-memory context"
                elif command.startswith("agent-memory register"):
                    command = "agent-memory projects"
                else:
                    continue
            if command not in compacted:
                compacted.append(command)
        payload["next_commands"] = compacted or ["agent-memory brief"]
    elif len(str(payload.get("project", "")).encode("utf-8")) > 128 or len(
        str(payload.get("scope", {}).get("project", "")).encode("utf-8")
    ) > 128:
        payload["project"] = None
        payload.setdefault("scope", {})["project"] = None
        payload["scope"]["identity_truncated"] = True
    elif len(payload.get("next_commands", [])) > 2:
        payload["next_commands"] = payload["next_commands"][:2]
    else:
        return False
    payload["truncated"] = True
    return True


def _minimal_navigation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    counts = payload.get("counts", {})
    return {
        "status": payload.get("status", "unknown"),
        "project": None,
        "source": payload.get("source", "unknown"),
        "scope": {"project": None, "source": payload.get("source", "unknown")},
        "counts": {
            key: counts.get(key, "unknown")
            for key in ("distinct", "global", "project", "core", "archive", "defaulted")
        },
        "navigation": [],
        "omitted": counts.get("distinct", "unknown"),
        "next_commands": ["agent-memory brief"],
        "truncated": True,
    }


def bounded_navigation_text(payload: dict[str, Any], budget: int = OUTPUT_BUDGET) -> str:
    """Prune optional navigation data until the UTF-8 packet fits."""
    item = copy.deepcopy(payload)
    while True:
        rendered = navigation_text(item) + "\n"
        if len(rendered.encode("utf-8")) <= budget:
            return rendered
        if not _prune_navigation_payload(item):
            rendered = navigation_text(_minimal_navigation_payload(item)) + "\n"
            if len(rendered.encode("utf-8")) <= budget:
                return rendered
            return "next: agent-memory brief\n"[:budget]


def bounded_json(payload: dict[str, Any], budget: int = OUTPUT_BUDGET) -> str:
    """Serialize a structured packet while enforcing the same byte budget."""
    item = copy.deepcopy(payload)
    while True:
        rendered = json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(rendered.encode("utf-8")) <= budget:
            return rendered
        if not _prune_navigation_payload(item):
            rendered = json.dumps(
                _minimal_navigation_payload(item),
                ensure_ascii=False,
                separators=(",", ":"),
            ) + "\n"
            if len(rendered.encode("utf-8")) <= budget:
                return rendered
            raise ValueError("navigation JSON budget is too small for the required command")


def _display(value: object, limit: int = 64) -> str:
    if value is None:
        text = ""
    elif isinstance(value, list):
        text = ", ".join(_display(item, limit=limit) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(value, float):
        text = f"{value:.3f}"
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    """Render a compact box-drawing table without terminal-width dependencies."""
    header_cells = [str(header) for header in headers]
    if not header_cells:
        return "(no columns)"
    raw_rows = [list(row) for row in rows]
    row_cells = [
        [_display(value) for value in row[: len(header_cells)]]
        + [""] * max(0, len(header_cells) - len(row))
        for row in raw_rows
    ]
    widths = [len(header) for header in header_cells]
    for row in row_cells:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    def border(left: str, middle: str, right: str) -> str:
        return left + middle.join("─" * (width + 2) for width in widths) + right

    def line(cells: list[str]) -> str:
        return (
            "│"
            + "│".join(f" {cell:<{width}} " for cell, width in zip(cells, widths))
            + "│"
        )

    output = [border("┌", "┬", "┐"), line(header_cells), border("├", "┼", "┤")]
    output.extend(line(row) for row in row_cells)
    output.append(border("└", "┴", "┘"))
    return "\n".join(output)


def _links_table(result: dict[str, Any]) -> str:
    document = result.get("document")
    sections: list[str] = []
    if isinstance(document, dict):
        sections.append(
            "Document\n"
            + render_table(
                ["id", "title", "path", "projects", "tags"],
                [
                    [
                        document.get("id", ""),
                        document.get("title", ""),
                        document.get("path", ""),
                        document.get("projects", ""),
                        document.get("tags", ""),
                    ]
                ],
            )
        )
    outbound = result.get("outbound", [])
    outbound_rows = []
    if isinstance(outbound, list):
        outbound_rows = [
            [
                "ok" if item.get("resolved") else "dangling",
                item.get("label", ""),
                item.get("href", ""),
                item.get("title") or "",
                item.get("path", ""),
            ]
            for item in outbound
            if isinstance(item, dict)
        ]
    sections.append(
        "Outbound\n"
        + render_table(
            ["status", "label", "href", "title", "path"],
            outbound_rows or [["", "(none)", "", "", ""]],
        )
    )
    inbound = result.get("inbound", [])
    inbound_rows = []
    if isinstance(inbound, list):
        inbound_rows = [
            [
                item.get("title", ""),
                item.get("label", ""),
                item.get("href", ""),
                item.get("path", ""),
            ]
            for item in inbound
            if isinstance(item, dict)
        ]
    sections.append(
        "Inbound\n"
        + render_table(
            ["title", "label", "href", "path"], inbound_rows or [["(none)", "", "", ""]]
        )
    )
    return "\n\n".join(sections)


def _doctor_table(result: dict[str, Any]) -> str:
    summary = result.get("summary")
    if isinstance(summary, dict):
        summary_text = " ".join(
            f"{key}={summary[key]}"
            for key in ("errors", "warnings", "info", "bindings", "documents")
            if key in summary
        )
    else:
        summary_text = _display(summary)
    sections = [f"status: {result.get('status', '')}", f"summary: {summary_text}"]
    checks = result.get("checks", [])
    rows = []
    if isinstance(checks, list):
        rows = [
            [
                item.get("severity", ""),
                item.get("code", ""),
                item.get("project", ""),
                item.get("message", ""),
                item.get("paths", ""),
                item.get("suggested_action", ""),
            ]
            for item in checks
            if isinstance(item, dict)
        ]
    sections.append(
        "Findings\n"
        + render_table(
            ["severity", "code", "project", "message", "paths", "suggested_action"],
            rows or [["", "(none)", "", "No problems found.", "", ""]],
        )
    )
    return "\n\n".join(sections)


def table_dump(command: str, result: object) -> str:
    """Choose concise columns for each CLI result shape."""
    if command in {"search", "list"} and isinstance(result, list):
        headers = ["id", "title", "brief", "projects", "tags"]
        if any(isinstance(item, dict) and "score" in item for item in result):
            headers.append("score")
        return render_table(
            headers,
            (
                [item.get(header, "") for header in headers]
                for item in result
                if isinstance(item, dict)
            ),
        )
    if command == "links" and isinstance(result, dict):
        return _links_table(result)
    if command == "projects" and isinstance(result, list):
        headers = [
            "project",
            "documents",
            "path",
            "capture_root",
            "memory_roots",
            "tags",
        ]
        return render_table(
            headers,
            (
                [item.get(header, "") for header in headers]
                for item in result
                if isinstance(item, dict)
            ),
        )
    if command == "tags" and isinstance(result, list):
        return render_table(
            ["tag", "count"],
            (
                [item.get("tag", ""), item.get("count", "")]
                for item in result
                if isinstance(item, dict)
            ),
        )
    if command == "browse" and isinstance(result, dict):
        rows = []
        for group in result.get("groups", []):
            if not isinstance(group, dict):
                continue
            scope = group.get("project", "")
            label = "shared" if scope == "_shared" else scope
            for document in group.get("documents", []):
                if isinstance(document, dict):
                    rows.append(
                        [
                            label,
                            document.get("title", ""),
                            document.get("brief", ""),
                            document.get("path", ""),
                        ]
                    )
        return render_table(
            ["scope", "title", "brief", "path"],
            rows or [["", "(none)", "", ""]],
        )
    if command == "doctor" and isinstance(result, dict):
        return _doctor_table(result)
    if isinstance(result, dict):
        return render_table(
            ["field", "value"], ((key, value) for key, value in result.items())
        )
    if isinstance(result, list):
        return render_table(["value"], ((item,) for item in result))
    return render_table(["value"], [(result,)])
