"""Resolve the project identity used by opening and navigation commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memory_config import MemoryError, flatten_bindings
from memory_identity import resolve_context as resolve_identity_context


def resolve_context(
    settings: dict[str, Any],
    *,
    path: Path | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Adapt the structured identity result to the CLI's stable dictionary shape."""
    if path is not None and project is not None:
        raise MemoryError("use either --project or --path, not both")

    bindings = flatten_bindings(settings)
    projects = sorted({binding.project for binding in bindings})
    source = "explicit_project" if project is not None else "path" if path is not None else "cwd"
    target = (path or Path.cwd()).expanduser().resolve(strict=False)
    identity = resolve_identity_context(settings, target, project)

    diagnostics = [
        {
            "code": item.code,
            "severity": item.severity,
            "message": item.message,
        }
        for item in identity.diagnostics
    ]
    registration_command = next(
        (item.command for item in identity.diagnostics if item.command), None
    )
    configured_projects = (
        []
        if identity.status == "resolved"
        else list(identity.aliases) or projects[:3]
    )
    return {
        "status": identity.status,
        "project": identity.project_id,
        "source": source,
        "path": None if project is not None else str(target),
        "configured_projects": configured_projects,
        "aliases": list(identity.aliases),
        "diagnostics": diagnostics,
        "registration_command": registration_command,
    }
