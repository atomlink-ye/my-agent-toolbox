import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[3] / "skills" / "agent-memory" / "scripts"
sys.path.insert(0, str(SCRIPTS))


class ContextContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "work tree" / "project"
        self.project.mkdir(parents=True)
        (self.project / "src").mkdir()
        self.settings = {
            "version": 1,
            "bindings": [
                {"path": str(self.project), "project": "demo", "memory": []},
                {"path": str(self.root / "other"), "project": "other", "memory": []},
            ],
            "shared": [],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_context_reports_identity_source(self):
        from memory_context import resolve_context

        resolved = resolve_context(self.settings, path=self.project / "src")
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["project"], "demo")
        self.assertEqual(resolved["source"], "path")

        explicit = resolve_context(self.settings, project="demo")
        self.assertEqual(explicit["source"], "explicit_project")

    def test_unregistered_context_exposes_names_only(self):
        from memory_context import resolve_context

        result = resolve_context(self.settings, path=self.root / "nowhere")
        self.assertEqual(result["status"], "unregistered")
        self.assertIsNone(result["project"])
        self.assertEqual(result["configured_projects"], ["demo", "other"])
        self.assertNotIn("memory", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
