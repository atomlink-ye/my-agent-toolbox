"""Resolve the project identity used by opening and navigation commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memory_config import (
    AmbiguousDescendantBindingError,
    MemoryError,
    UnboundPathError,
    flatten_bindings,
    resolve_binding,
)


def resolve_context(
    settings: dict[str, Any],
    *,
    path: Path | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Resolve a project and explain which identity source was used.

    Unregistered results deliberately expose only up to three configured project
    identifiers.  They never include another project's documents or metadata.
    """
    if path is not None and project is not None:
        raise MemoryError("use either --project or --path, not both")

    bindings = flatten_bindings(settings)
    projects = sorted({binding.project for binding in bindings})
    if project is not None:
        if project in projects:
            return {
                "status": "resolved",
                "project": project,
                "source": "explicit_project",
                "path": None,
                "configured_projects": [],
            }
        return {
            "status": "unregistered",
            "project": None,
            "source": "explicit_project",
            "path": None,
            "configured_projects": projects[:3],
        }

    source = "path" if path is not None else "cwd"
    target = (path or Path.cwd()).expanduser().resolve(strict=False)
    try:
        binding = resolve_binding(settings, target)
    except (UnboundPathError, AmbiguousDescendantBindingError):
        return {
            "status": "unregistered",
            "project": None,
            "source": source,
            "path": str(target),
            "configured_projects": projects[:3],
        }
    return {
        "status": "resolved",
        "project": binding.project,
        "source": source,
        "path": str(target),
        "configured_projects": [],
    }
