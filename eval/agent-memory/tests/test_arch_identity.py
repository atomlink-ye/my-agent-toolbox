import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
