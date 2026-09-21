import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[3] / "skills" / "agent-memory" / "scripts" / "agent_memory.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("agent_memory_navigation_tests", MODULE_PATH)
am = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = am
assert spec.loader is not None
spec.loader.exec_module(am)


class NavigationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.memory = self.root / "memory"
        self.shared = self.root / "shared"
        for path in (self.project, self.memory, self.shared):
            path.mkdir()
        self.settings_path = self.root / "settings.json"
        self.settings_path.write_text(json.dumps({
            "version": 1,
            "database": str(self.root / "index.sqlite3"),
            "shared": [str(self.shared)],
            "bindings": [{"path": str(self.project), "project": "demo", "memory": [str(self.memory)]}],
        }), encoding="utf-8")
        (self.memory / "setup.md").write_text("---\ntitle: Setup\ntags: [setup, conventions]\n---\n\nInstall safely.\n", encoding="utf-8")
        (self.shared / "global.md").write_text("---\ntitle: Global Rules\ntags: [conventions]\n---\n\nReusable rules.\n", encoding="utf-8")
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

    def test_no_match_json_keeps_array_and_navigation_on_stderr(self):
        code, out, err = self.cli("--json", "search", "orchard-nebula", "--path", str(self.project))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])
        self.assertIn("status=no_match", err)
        self.assertIn("navigation", err)
        self.assertIn("next:", err)
        self.assertLessEqual(len((out + err).encode()), 2048)

    def test_envelope_is_one_object_and_navigation_is_not_results(self):
        code, out, err = self.cli("--envelope", "search", "x" * 10000, "--path", str(self.project))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["status"], "no_match")
        self.assertEqual(payload["results"], [])
        self.assertTrue(payload["navigation"])
        self.assertLessEqual(len(out.encode()), 2048)

    def test_all_empty_result_formats_have_navigation(self):
        for flag in ("--text", "--table", "--yaml", "--json"):
            with self.subTest(flag=flag):
                code, out, err = self.cli(flag, "search", "no-such-term", "--path", str(self.project))
                self.assertEqual(code, 0)
                self.assertIn("status=no_match", err)
                self.assertLessEqual(len((out + err).encode()), 2048)

    def test_corrupt_database_is_not_reported_as_no_match(self):
        Path(self.root / "index.sqlite3").write_bytes(b"not a database")
        code, out, err = self.cli("search", "anything", "--path", str(self.project))
        self.assertNotEqual(code, 0)
        self.assertNotIn("status=no_match", out + err)

    def test_positive_compact_output_does_not_gain_navigation(self):
        code, out, err = self.cli("search", "Install", "--path", str(self.project))
        self.assertEqual(code, 0)
        self.assertIn("Setup", out)
        self.assertNotIn("navigation", out + err)

    def test_brief_context_terms_do_not_change_search_hit_truth(self):
        code, before, _ = self.cli("--json", "search", "Install", "--path", str(self.project))
        self.assertEqual(code, 0)
        with patch.object(am, "current_context_terms", side_effect=AssertionError("search must not extract brief context")):
            code, after, _ = self.cli("--json", "search", "Install", "--path", str(self.project))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(after), json.loads(before))

    def test_cwd_no_match_identity_and_count_match_brief(self):
        with patch.object(Path, "cwd", return_value=self.project):
            brief_code, brief_out, brief_err = self.cli("brief")
            search_code, search_out, search_err = self.cli("search", "orchard-nebula")

        self.assertEqual((brief_code, search_code), (0, 0))
        self.assertEqual(brief_err, "")
        self.assertIn("(no results)", search_out)
        self.assertIn("project=demo source=cwd", brief_out)
        self.assertIn("project=demo source=cwd", search_err)
        self.assertIn("counts: distinct=2 global=1 project=1", brief_out)
        self.assertIn("counts: distinct=2 global=1 project=1", search_err)
        self.assertIn("tags:", search_err)
        self.assertIn("navigation:", search_err)
        self.assertIn("next: agent-memory context --project demo", search_err)


if __name__ == "__main__":
    unittest.main()
