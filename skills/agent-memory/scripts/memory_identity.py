"""Read-only project identity resolution for Agent Memory."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from memory_config import (
    Binding,
    MemoryError,
    MemoryRoot,
    SHARED_SCOPE,
    _dedupe,
    _is_within,
    flatten_bindings,
    shared_roots,
)

ResolutionStatus = Literal["resolved", "unregistered", "ambiguous"]


@dataclass(frozen=True)
class IdentityDiagnostic:
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    path: Path | None = None

    def __contains__(self, value: object) -> bool:
        """Keep diagnostics pleasant for both structured and textual callers."""
        return isinstance(value, str) and value in f"{self.code} {self.message}"


@dataclass(frozen=True)
class ResolvedContext:
    project_id: str | None
    status: ResolutionStatus
    aliases: tuple[str, ...]
    global_roots: tuple[MemoryRoot, ...]
    project_roots: tuple[MemoryRoot, ...]
    diagnostics: tuple[IdentityDiagnostic, ...]


@dataclass(frozen=True)
class _GitIdentity:
    common_dir: Path
    worktree_root: Path | None


def _run_git(path: Path, *args: str) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(path), "rev-parse", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 126, "", str(exc)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _git_identity(path: Path) -> _GitIdentity | None:
    code, common, _ = _run_git(
        path, "--path-format=absolute", "--git-common-dir"
    )
    if code or not common:
        return None
    common_dir = Path(common).resolve(strict=False)
    top_code, top, _ = _run_git(path, "--path-format=absolute", "--show-toplevel")
    worktree_root = Path(top).resolve(strict=False) if not top_code and top else None
    return _GitIdentity(common_dir=common_dir, worktree_root=worktree_root)


def _path_problem(path: Path) -> IdentityDiagnostic | None:
    try:
        if not path.exists():
            return IdentityDiagnostic(
                "path_missing", "error", f"path does not exist: {path}", path
            )
        if not path.is_dir():
            return IdentityDiagnostic(
                "path_not_directory", "error", f"path is not a directory: {path}", path
            )
        current = path
        while True:
            mode = current.stat().st_mode
            if stat.S_ISDIR(mode) and mode & 0o111 == 0:
                return IdentityDiagnostic(
                    "path_permission_denied",
                    "error",
                    f"path has no directory traversal permission: {current}",
                    current,
                )
            if current.parent == current:
                break
            current = current.parent
        if not os.access(path, os.R_OK | os.X_OK):
            return IdentityDiagnostic(
                "path_permission_denied",
                "error",
                f"path lacks read or traversal permission: {path}",
                path,
            )
    except (OSError, PermissionError) as exc:
        return IdentityDiagnostic(
            "path_unreadable", "error", f"cannot inspect path {path}: {exc}", path
        )
    return None


def _global_roots(settings: dict[str, Any]) -> tuple[MemoryRoot, ...]:
    raw_origin = getattr(settings, "source_path", None)
    if isinstance(raw_origin, (str, os.PathLike)):
        settings_path = Path(raw_origin).resolve(strict=False)
    else:
        # The required public signature has no settings_path. Absolute paths are
        # unaffected; literal settings with relative shared roots use cwd.
        settings_path = (Path.cwd() / "settings.json").resolve(strict=False)
    return tuple(shared_roots(settings, settings_path))


def _binding_git_identities(
    bindings: Iterable[Binding],
) -> dict[Path, _GitIdentity]:
    identities: dict[Path, _GitIdentity] = {}
    for binding in bindings:
        if binding.path.is_dir():
            identity = _git_identity(binding.path)
            if identity is not None:
                identities[binding.path] = identity
    return identities


def _deepest(matches: Iterable[Binding]) -> tuple[Binding, ...]:
    values = tuple(matches)
    if not values:
        return ()
    depth = max(item.depth for item in values)
    return tuple(item for item in values if item.depth == depth)


def _matching_bindings(
    target: Path,
    target_git: _GitIdentity | None,
    bindings: tuple[Binding, ...],
    binding_git: dict[Path, _GitIdentity],
) -> tuple[tuple[Binding, ...], tuple[str, ...]]:
    direct = _deepest(item for item in bindings if _is_within(target, item.path))
    if direct:
        if target_git is None:
            return direct, _dedupe(item.project for item in direct)
        aliases = _dedupe(
            item.project
            for item in bindings
            if binding_git.get(item.path) is not None
            and binding_git[item.path].common_dir == target_git.common_dir
        )
        return direct, aliases

    if target_git is not None:
        same_repo = tuple(
            item
            for item in bindings
            if binding_git.get(item.path) is not None
            and binding_git[item.path].common_dir == target_git.common_dir
        )
        aliases = _dedupe(item.project for item in same_repo)
        if same_repo and target_git.worktree_root is not None:
            try:
                relative = target.relative_to(target_git.worktree_root)
            except ValueError:
                relative = None
            if relative is not None:
                translated = []
                for item in same_repo:
                    root = binding_git[item.path].worktree_root
                    if root is not None and _is_within(root / relative, item.path):
                        translated.append(item)
                winners = _deepest(translated)
                if winners:
                    return winners, aliases
        projects = _dedupe(item.project for item in same_repo)
        if len(projects) == 1:
            return same_repo, aliases
        return (), aliases

    descendants = tuple(item for item in bindings if _is_within(item.path, target))
    if len(descendants) == 1:
        return descendants, (descendants[0].project,)
    return (), _dedupe(item.project for item in descendants)


def _roots_for_projects(
    bindings: Iterable[Binding], projects: Iterable[str]
) -> tuple[MemoryRoot, ...]:
    selected = set(projects)
    roots: list[MemoryRoot] = []
    for binding in bindings:
        if binding.project not in selected:
            continue
        roots.extend(
            MemoryRoot(
                path=location.path,
                scope=binding.project,
                tags=_dedupe((*binding.tags, *location.tags)),
            )
            for location in binding.memory_roots
        )
    return tuple(roots)


def resolve_context(
    settings: dict[str, Any], path: str | os.PathLike[str], explicit_project: str | None = None
) -> ResolvedContext:
    """Resolve the visible global/project memory scopes without writing state.

    Git's canonical common directory is the local repository locator. It makes
    a repository root, its descendants, and linked worktrees one identity while
    intentionally keeping independent clones separate. Existing v1 project
    names remain the scope IDs until a future explicit registry migration.
    """
    global_roots = _global_roots(settings)
    target = Path(os.path.expandvars(os.path.expanduser(os.fspath(path))))
    if not target.is_absolute():
        target = Path.cwd() / target
    try:
        target = target.resolve(strict=False)
    except (OSError, PermissionError) as exc:
        diagnostic = IdentityDiagnostic(
            "path_unreadable",
            "error",
            f"cannot canonicalize path {target}: {exc}",
            target,
        )
        return ResolvedContext(None, "unregistered", (), global_roots, (), (diagnostic,))

    problem = _path_problem(target)
    if problem is not None:
        return ResolvedContext(None, "unregistered", (), global_roots, (), (problem,))

    try:
        bindings = tuple(flatten_bindings(settings))
    except MemoryError as exc:
        diagnostic = IdentityDiagnostic(
            "invalid_bindings", "error", f"cannot resolve invalid bindings: {exc}"
        )
        return ResolvedContext(None, "ambiguous", (), global_roots, (), (diagnostic,))

    target_git = _git_identity(target)
    binding_git = _binding_git_identities(bindings)
    winners, aliases = _matching_bindings(target, target_git, bindings, binding_git)

    if explicit_project is not None:
        requested = explicit_project.strip()
        explicit_matches = tuple(item for item in bindings if item.project == requested)
        if not requested or not explicit_matches:
            diagnostic = IdentityDiagnostic(
                "unknown_project",
                "error",
                f"explicit project is not registered: {explicit_project!r}",
                target,
            )
            return ResolvedContext(None, "ambiguous", aliases, global_roots, (), (diagnostic,))
        diagnostic = IdentityDiagnostic(
            "explicit_project",
            "info",
            f"using explicit v1 project scope {requested!r}",
            target,
        )
        return ResolvedContext(
            requested,
            "resolved",
            _dedupe((*aliases, requested)),
            global_roots,
            _roots_for_projects(bindings, (requested,)),
            (diagnostic,),
        )

    winner_projects = _dedupe(item.project for item in winners)
    if len(winner_projects) == 1:
        project = winner_projects[0]
        locator = target_git.common_dir if target_git is not None else target
        diagnostic = IdentityDiagnostic(
            "resolved_v1_binding",
            "info",
            f"resolved project {project!r} through a v1 binding",
            locator,
        )
        return ResolvedContext(
            project,
            "resolved",
            aliases or (project,),
            global_roots,
            _roots_for_projects(bindings, (project,)),
            (diagnostic,),
        )

    if aliases:
        diagnostic = IdentityDiagnostic(
            "ambiguous_identity",
            "error",
            "repository/path identity maps to multiple v1 projects: "
            + ", ".join(aliases),
            target_git.common_dir if target_git is not None else target,
        )
        return ResolvedContext(None, "ambiguous", aliases, global_roots, (), (diagnostic,))

    if target_git is not None:
        digest = hashlib.sha256(os.fsencode(target_git.common_dir)).hexdigest()[:12]
        diagnostic = IdentityDiagnostic(
            "unregistered_git_identity",
            "warning",
            f"Git common directory is not registered (fingerprint {digest})",
            target_git.common_dir,
        )
    else:
        digest = hashlib.sha256(os.fsencode(target)).hexdigest()[:12]
        diagnostic = IdentityDiagnostic(
            "unregistered_path_identity",
            "warning",
            f"non-Git resolved path is not registered (fingerprint {digest})",
            target,
        )
    return ResolvedContext(None, "unregistered", (), global_roots, (), (diagnostic,))
