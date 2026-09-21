import importlib
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[3] / "skills" / "agent-memory" / "scripts"
sys.path.insert(0, str(SCRIPTS))
store = importlib.import_module("memory_store")


class NavigationInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.global_root = self.root / "global"
        self.project_root = self.root / "project"
        self.other_root = self.root / "other"
        for path in (self.global_root, self.project_root, self.other_root):
            path.mkdir()
        (self.global_root / "shared.md").write_text(
            "---\ntags: [common, alpha, beta, delta, epsilon, gamma, zeta]\n---\n"
            "# Shared\nShared route.\n"
        )
        (self.project_root / "project.md").write_text(
            "---\ntags: [alpha, project]\n---\n# Project\nProject route.\n"
        )
        (self.other_root / "secret.md").write_text(
            "---\ntags: [secret]\n---\n# Secret\nMust not leak.\n"
        )

    def tearDown(self):
        self.temp.cleanup()

    def context(self, status="resolved"):
        return {
            "status": status,
            "project_id": "demo" if status == "resolved" else None,
            "global_roots": [{"path": self.global_root, "tags": ["global-root"]}],
            "project_roots": [{"path": self.project_root, "tags": []}],
        }

    def test_inventory_counts_union_scopes_and_limits_tags(self):
        inventory = store.navigation_inventory(self.context(), {})
        self.assertEqual(
            inventory["counts"], {"distinct": 2, "global": 1, "project": 1}
        )
        self.assertLessEqual(len(inventory["tags"]), 5)
        self.assertEqual(len(inventory["tags"]), 5)
        self.assertEqual(inventory["tags"][0], {"tag": "alpha", "count": 2})
        self.assertEqual(
            {Path(item["path"]).name for item in inventory["route_candidates"]},
            {"shared.md", "project.md"},
        )

    def test_global_and_project_filters_do_not_leak(self):
        global_only = store.navigation_inventory(self.context(), {"global": True})
        self.assertEqual(global_only["counts"], {"distinct": 1, "global": 1, "project": 0})
        self.assertEqual(
            [Path(item["path"]).name for item in global_only["route_candidates"]],
            ["shared.md"],
        )
        project_only = store.navigation_inventory(
            self.context(), {"include_global": False, "tags": ["project"]}
        )
        self.assertEqual(project_only["counts"], {"distinct": 1, "global": 0, "project": 1})
        self.assertEqual(len(project_only["route_candidates"]), 1)
        self.assertNotIn("secret", repr(project_only))

    def test_unresolved_context_never_expands_to_projects(self):
        inventory = store.navigation_inventory(self.context("unregistered"), {})
        self.assertEqual(inventory["counts"], {"distinct": 1, "global": 1, "project": None})
        self.assertEqual(
            [Path(item["path"]).name for item in inventory["route_candidates"]],
            ["shared.md"],
        )

    def test_unknown_is_null_not_zero(self):
        inventory = store.navigation_inventory(
            {"status": "unavailable", "project_id": None}, {}
        )
        self.assertEqual(
            inventory["counts"], {"distinct": None, "global": None, "project": None}
        )
        self.assertEqual(inventory["tags"], [])
        self.assertEqual(inventory["route_candidates"], [])

    def test_filters_apply_to_counts_tags_and_routes(self):
        inventory = store.navigation_inventory(self.context(), {"tags": ["common"]})
        self.assertEqual(inventory["counts"], {"distinct": 1, "global": 1, "project": 0})
        self.assertEqual(
            [Path(item["path"]).name for item in inventory["route_candidates"]],
            ["shared.md"],
        )


if __name__ == "__main__":
    unittest.main()
