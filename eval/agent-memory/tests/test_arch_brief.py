import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[3] / "skills" / "agent-memory" / "scripts" / "agent_memory.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("agent_memory_brief_tests", MODULE_PATH)
am = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = am
assert spec.loader is not None
spec.loader.exec_module(am)


class BriefContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root
        for index in range(8):
            self.project /= ("项目" * 8) + str(index)
        self.project /= "project with spaces"
        self.memory = self.root / "memory"
        self.shared = self.root / "shared"
        for path in (self.project, self.memory, self.shared):
            path.mkdir(parents=True)
        self.settings_path = self.root / "settings.json"
        self.settings_path.write_text(json.dumps({
            "version": 1,
            "database": str(self.root / "index.sqlite3"),
            "shared": [str(self.shared)],
            "bindings": [{"path": str(self.project), "project": "demo", "memory": [str(self.memory)]}],
        }), encoding="utf-8")
        for index in range(8):
            target = self.shared if index < 3 else self.memory
            (target / f"note-{index}.md").write_text(
                f"---\ntitle: Note {index}\ntags: [tag-{index}, common]\n---\n\nBody {index}.\n", encoding="utf-8"
            )
        settings = am.load_settings(self.settings_path)
        conn = am.connect_db(am.database_path(settings, self.settings_path))
        am.sync_index(conn, am.collect_memory_roots(settings, self.settings_path))
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = am.main(["--settings", str(self.settings_path), *args])
        return code, out.getvalue(), err.getvalue()

    def test_brief_from_cwd_is_bounded_and_actionable(self):
        with patch.object(Path, "cwd", return_value=self.project), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            code, out, err = self.cli("brief")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("status=ok", out)
        self.assertIn("project=demo", out)
        self.assertIn("distinct=8", out)
        self.assertIn("global=3", out)
        self.assertIn("project=5", out)
        self.assertIn("omitted:", out)
        self.assertIn("navigation, not search hits", out)
        self.assertIn("next:", out)
        self.assertLessEqual(len(out.encode()), 2048)

    def test_brief_path_and_context_json(self):
        code, out, _ = self.cli("--json", "brief", "--path", str(self.project))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["project"], "demo")
        self.assertEqual(payload["counts"]["distinct"], 8)
        code, out, _ = self.cli("--json", "context", "--path", str(self.project))
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(json.loads(out)["navigation"]), 1)

    def test_empty_and_unregistered_are_distinct(self):
        for path in self.memory.glob("*.md"):
            path.unlink()
        for path in self.shared.glob("*.md"):
            path.unlink()
        settings = am.load_settings(self.settings_path)
        conn = am.connect_db(am.database_path(settings, self.settings_path))
        am.sync_index(conn, am.collect_memory_roots(settings, self.settings_path))
        conn.close()
        code, out, _ = self.cli("brief", "--path", str(self.project))
        self.assertEqual(code, 0)
        self.assertIn("status=empty", out)
        code, out, _ = self.cli("brief", "--path", str(self.root / "unknown"))
        self.assertEqual(code, 0)
        self.assertIn("status=unregistered", out)
        self.assertIn("configured_projects", out)


if __name__ == "__main__":
    unittest.main()
