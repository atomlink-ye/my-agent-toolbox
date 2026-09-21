import importlib.util
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).parents[3] / "skills" / "agent-memory" / "scripts"
sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "memory_identity.py"


def load_identity_module():
    spec = importlib.util.spec_from_file_location("memory_identity", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def git(*args, cwd=None):
    subprocess.run(
        ["git", *map(str, args)],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class IdentityResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", self.repo)
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        git("-C", self.repo, "add", "README.md")
        git(
            "-C",
            self.repo,
            "-c",
            "user.name=Identity Test",
            "-c",
            "user.email=identity@example.invalid",
            "commit",
            "-m",
            "fixture",
        )
        (self.repo / "src").mkdir()
        self.project_memory = self.root / "project-memory"
        self.global_memory = self.root / "global-memory"
        self.project_memory.mkdir()
        self.global_memory.mkdir()
        self.settings = {
            "version": 1,
            "shared": [{"path": str(self.global_memory), "tags": ["global"]}],
            "bindings": [
                {
                    "path": str(self.repo),
                    "project": "demo",
                    "memory": [str(self.project_memory)],
                }
            ],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_repo_root_subdirectory_and_linked_worktree_share_identity(self):
        identity = load_identity_module()
        linked = self.root / "linked"
        git("-C", self.repo, "worktree", "add", "--detach", linked)

        root = identity.resolve_context(self.settings, self.repo)
        child = identity.resolve_context(self.settings, self.repo / "src")
        worktree = identity.resolve_context(self.settings, linked)

        self.assertEqual(root.status, "resolved")
        self.assertEqual(root.project_id, child.project_id)
        self.assertEqual(root.project_id, worktree.project_id)
        self.assertEqual(root.aliases, ("demo",))
        self.assertEqual({item.path for item in root.global_roots}, {self.global_memory})
        self.assertEqual({item.path for item in root.project_roots}, {self.project_memory})

    def test_missing_linked_worktree_binding_is_recovered_from_repo_identity(self):
        identity = load_identity_module()
        stale = self.root / "stale-linked"
        git("-C", self.repo, "worktree", "add", "--detach", stale)
        settings = json.loads(json.dumps(self.settings))
        settings["bindings"][0]["path"] = str(stale)

        # Simulate an ephemeral worktree directory disappearing while Git's
        # repository metadata and the user's explicit binding remain.
        for path in sorted(stale.rglob("*"), reverse=True):
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
        stale.rmdir()

        result = identity.resolve_context(settings, self.repo)

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.project_id, "demo")
        self.assertTrue(any("stale" in item.code for item in result.diagnostics))

    def test_stale_worktree_metadata_from_an_independent_clone_is_not_used(self):
        identity = load_identity_module()
        clone_a = self.root / "clone-a"
        clone_b = self.root / "clone-b"
        git("clone", self.repo, clone_a)
        git("clone", self.repo, clone_b)
        stale = self.root / "clone-a-stale-worktree"
        git("-C", clone_a, "worktree", "add", "--detach", stale)
        settings = {
            "version": 1,
            "shared": [],
            "bindings": [{
                "path": str(stale),
                "project": "demo",
                "memory": [str(self.project_memory)],
            }],
        }
        for path in sorted(stale.rglob("*"), reverse=True):
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
        stale.rmdir()

        result = identity.resolve_context(settings, clone_b)

        self.assertEqual(result.status, "unregistered")
        self.assertFalse(any("stale" in item.code for item in result.diagnostics))

    def test_symlink_path_is_canonicalized(self):
        identity = load_identity_module()
        alias = self.root / "repo-alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        direct = identity.resolve_context(self.settings, self.repo / "src")
        through_alias = identity.resolve_context(self.settings, alias / "src")
        self.assertEqual(direct.project_id, through_alias.project_id)
        self.assertEqual(through_alias.status, "resolved")

    def test_non_git_directories_have_distinct_path_identities(self):
        identity = load_identity_module()
        one = self.root / "plain" / "one"
        two = self.root / "plain" / "two"
        one.mkdir(parents=True)
        two.mkdir()
        first = identity.resolve_context({"version": 1, "shared": [], "bindings": []}, one)
        second = identity.resolve_context({"version": 1, "shared": [], "bindings": []}, two)
        self.assertEqual(first.status, "unregistered")
        self.assertEqual(first.project_roots, ())
        self.assertIsNone(first.project_id)
        self.assertIsNone(second.project_id)
        self.assertNotEqual(first.diagnostics[-1].path, second.diagnostics[-1].path)

    def test_clones_with_the_same_remote_are_distinct(self):
        identity = load_identity_module()
        origin = self.root / "origin.git"
        git("init", "--bare", origin)
        clone_a = self.root / "clone-a"
        clone_b = self.root / "clone-b"
        git("clone", origin, clone_a)
        git("clone", origin, clone_b)
        empty = {"version": 1, "shared": [], "bindings": []}
        a = identity.resolve_context(empty, clone_a)
        b = identity.resolve_context(empty, clone_b)
        self.assertIsNone(a.project_id)
        self.assertIsNone(b.project_id)
        self.assertNotEqual(a.diagnostics[-1].path, b.diagnostics[-1].path)
        self.assertTrue(any("common directory" in item for item in a.diagnostics))

    def test_missing_and_inaccessible_paths_fail_closed_with_diagnostics(self):
        identity = load_identity_module()
        missing = identity.resolve_context(self.settings, self.root / "does-not-exist")
        self.assertEqual(missing.status, "unregistered")
        self.assertIsNone(missing.project_id)
        self.assertEqual(missing.project_roots, ())
        self.assertTrue(any("does not exist" in item for item in missing.diagnostics))

        locked = self.root / "locked"
        locked.mkdir()
        locked.chmod(0)
        try:
            denied = identity.resolve_context(self.settings, locked)
        finally:
            locked.chmod(stat.S_IRWXU)
        self.assertEqual(denied.status, "unregistered")
        self.assertIsNone(denied.project_id)
        self.assertEqual(denied.project_roots, ())
        self.assertTrue(any("permission" in item for item in denied.diagnostics))

    def test_nested_v1_bindings_and_same_root_in_multiple_scopes(self):
        identity = load_identity_module()
        workspace = self.root / "workspace"
        project_a = workspace / "a"
        project_b = workspace / "b"
        project_a.mkdir(parents=True)
        project_b.mkdir()
        shared_physical_root = self.root / "shared-physical-root"
        shared_physical_root.mkdir()
        settings = {
            "version": 1,
            "shared": [],
            "bindings": [
                {
                    "path": str(workspace),
                    "project": "workspace",
                    "memory": [],
                    "projects": [
                        {"path": "a", "project": "a", "memory": [str(shared_physical_root)]},
                        {"path": "b", "project": "b", "memory": [str(shared_physical_root)]},
                    ],
                }
            ],
        }
        context_a = identity.resolve_context(settings, project_a)
        context_b = identity.resolve_context(settings, project_b)
        self.assertEqual(context_a.project_id, "a")
        self.assertEqual(context_b.project_id, "b")
        self.assertEqual(context_a.project_roots[0].scope, "a")
        self.assertEqual(context_b.project_roots[0].scope, "b")

    def test_conflicting_exact_v1_bindings_are_ambiguous(self):
        identity = load_identity_module()
        settings = json.loads(json.dumps(self.settings))
        settings["bindings"].append(
            {"path": str(self.repo), "project": "other", "memory": []}
        )
        ambiguous = identity.resolve_context(settings, self.repo)
        self.assertEqual(ambiguous.status, "ambiguous")
        self.assertEqual(ambiguous.project_roots, ())
        self.assertEqual(set(ambiguous.aliases), {"demo", "other"})

        selected = identity.resolve_context(settings, self.repo, explicit_project="other")
        self.assertEqual(selected.status, "resolved")
        self.assertEqual(selected.project_id, "other")

    def test_linked_worktree_preserves_nested_binding_by_relative_path(self):
        identity = load_identity_module()
        nested_memory = self.root / "nested-memory"
        nested_memory.mkdir()
        settings = json.loads(json.dumps(self.settings))
        settings["bindings"][0]["projects"] = [
            {"path": "src", "project": "nested", "memory": [str(nested_memory)]}
        ]
        linked = self.root / "nested-linked"
        git("-C", self.repo, "worktree", "add", "--detach", linked)
        (linked / "src").mkdir(exist_ok=True)

        direct = identity.resolve_context(settings, self.repo / "src")
        through_link = identity.resolve_context(settings, linked / "src")
        self.assertEqual(direct.project_id, "nested")
        self.assertEqual(through_link.project_id, "nested")
        self.assertEqual(through_link.project_roots[0].path, nested_memory)

    def test_resolution_does_not_modify_repository_or_settings(self):
        identity = load_identity_module()
        settings_before = json.dumps(self.settings, sort_keys=True)

        def snapshot():
            return {
                path.relative_to(self.root): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in self.root.rglob("*")
                if path.is_file()
            }

        files_before = snapshot()
        result = identity.resolve_context(self.settings, self.repo / "src")
        self.assertEqual(result.status, "resolved")
        self.assertEqual(json.dumps(self.settings, sort_keys=True), settings_before)
        self.assertEqual(snapshot(), files_before)

    def test_unregistered_repo_with_known_memory_root_proposes_explicit_registration(self):
        identity = load_identity_module()
        learnings = self.repo / ".learnings"
        learnings.mkdir()

        result = identity.resolve_context(
            {"version": 1, "shared": [], "bindings": []}, self.repo
        )

        self.assertEqual(result.status, "unregistered")
        proposal = next(item for item in result.diagnostics if item.code == "registration_suggestion")
        self.assertIn("agent-memory", proposal.message)
        self.assertIn("register", proposal.message)
        self.assertIn(str(self.repo), proposal.message)
        self.assertIn(str(learnings), proposal.message)
        self.assertIn("--memory-root", proposal.command)

    def test_non_git_task_is_resolved_by_one_exact_configured_learnings_root(self):
        identity = load_identity_module()
        task = self.root / "real-task"
        learnings = task / ".learnings"
        learnings.mkdir(parents=True)
        settings = {
            "version": 1,
            "shared": [],
            "bindings": [{
                "path": str(self.root / "expired-worktree"),
                "project": "arcp",
                "memory": [str(learnings)],
            }],
        }

        result = identity.resolve_context(settings, task)

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.project_id, "arcp")
        self.assertEqual(result.project_roots[0].path, learnings)

    def test_ambiguous_learnings_root_does_not_choose_a_project(self):
        identity = load_identity_module()
        task = self.root / "ambiguous-task"
        learnings = task / ".learnings"
        learnings.mkdir(parents=True)
        settings = {
            "version": 1,
            "shared": [],
            "bindings": [
                {"path": str(self.root / "old-a"), "project": "a", "memory": [str(learnings)]},
                {"path": str(self.root / "old-b"), "project": "b", "memory": [str(learnings)]},
            ],
        }

        result = identity.resolve_context(settings, task)

        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.project_id)
        self.assertEqual(set(result.aliases), {"a", "b"})
        self.assertTrue(any(item.code == "ambiguous_memory_root" for item in result.diagnostics))

    def test_non_git_learnings_discovery_stops_at_home_but_works_outside_home(self):
        identity = load_identity_module()
        home = self.root / "fake-home"
        home_learnings = home / ".learnings"
        hermes = home / ".hermes"
        home_learnings.mkdir(parents=True)
        hermes.mkdir()
        external_task = self.root / "external-task"
        (external_task / ".learnings").mkdir(parents=True)
        nested = external_task / "src"
        nested.mkdir()
        empty_settings = {"version": 1, "shared": [], "bindings": []}

        with patch.object(Path, "home", return_value=home):
            from_hermes = identity.resolve_context(empty_settings, hermes)
            from_task = identity.resolve_context(empty_settings, external_task)
            from_subdirectory = identity.resolve_context(empty_settings, nested)

        self.assertEqual(from_hermes.status, "unregistered")
        self.assertFalse(any(item.code == "registration_suggestion" for item in from_hermes.diagnostics))
        for result in (from_task, from_subdirectory):
            self.assertEqual(result.status, "unregistered")
            suggestion = next(
                item for item in result.diagnostics if item.code == "registration_suggestion"
            )
            command = shlex.split(suggestion.command)
            self.assertEqual(command[command.index("--path") + 1], str(external_task))
            self.assertEqual(command[command.index("--memory-root") + 1], str(external_task / ".learnings"))

    def test_concurrent_explicit_registrations_preserve_both_bindings(self):
        import memory_config

        settings_path = self.root / "concurrent-settings.json"
        settings_path.write_text(
            json.dumps({"version": 1, "shared": [], "bindings": []}),
            encoding="utf-8",
        )
        projects = [self.root / name for name in ("first-task", "second-task")]
        for project in projects:
            (project / ".learnings").mkdir(parents=True)

        first_loaded = threading.Event()
        second_lock_attempt = threading.Event()
        second_loaded = threading.Event()
        release_first = threading.Event()
        errors = []
        original_load = memory_config.load_settings
        original_flock = memory_config.fcntl.flock

        def delayed_load(path):
            result = original_load(path)
            thread_name = threading.current_thread().name
            if thread_name == "registration-first":
                first_loaded.set()
                if not release_first.wait(5):
                    raise TimeoutError("test did not release the first registration")
            elif thread_name == "registration-second":
                second_loaded.set()
            return result

        def observed_flock(fd, operation):
            if (
                threading.current_thread().name == "registration-second"
                and operation & memory_config.fcntl.LOCK_EX
            ):
                second_lock_attempt.set()
            return original_flock(fd, operation)

        def register(project):
            try:
                memory_config.register_project(
                    settings_path, project, project.name, project / ".learnings"
                )
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=register, args=(projects[0],), name="registration-first")
        second = threading.Thread(target=register, args=(projects[1],), name="registration-second")
        with patch.object(memory_config, "load_settings", delayed_load), patch.object(
            memory_config.fcntl, "flock", observed_flock
        ):
            first.start()
            try:
                self.assertTrue(first_loaded.wait(5))
                second.start()
                self.assertTrue(second_lock_attempt.wait(5))
                self.assertFalse(second_loaded.is_set())
            finally:
                release_first.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        updated = original_load(settings_path)
        self.assertEqual(
            {item.project for item in memory_config.flatten_bindings(updated)},
            {"first-task", "second-task"},
        )

    def test_memory_config_exposes_the_identity_resolver(self):
        import memory_config

        result = memory_config.resolve_context(self.settings, self.repo)
        self.assertEqual(result.project_id, "demo")

    def test_loaded_settings_resolve_relative_global_roots_from_settings_file(self):
        import memory_config

        config_dir = self.root / "config"
        config_dir.mkdir()
        settings_path = config_dir / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "shared": [{"path": "relative-global"}],
                    "bindings": self.settings["bindings"],
                }
            ),
            encoding="utf-8",
        )
        loaded = memory_config.load_settings(settings_path)
        result = memory_config.resolve_context(loaded, self.repo)
        self.assertEqual(
            result.global_roots[0].path, (config_dir / "relative-global").resolve()
        )


if __name__ == "__main__":
    unittest.main()
