import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

MODULE_PATH = Path(__file__).parents[3] / "skills" / "agent-memory" / "scripts" / "agent_memory.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("agent_memory_format_tests", MODULE_PATH)
am = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = am
assert spec.loader is not None
spec.loader.exec_module(am)

from memory_format import bounded_json, bounded_navigation_text, table_dump, yaml_dump


class AgentMemoryOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "workspace" / "project"
        self.memory = self.root / "memory"
        self.project.mkdir(parents=True)
        self.memory.mkdir()
        self.settings = self.root / "settings.json"
        self.settings.write_text(
            json.dumps({
                "version": 1,
                "database": str(self.root / "index.sqlite3"),
                "shared": [],
                "bindings": [{"path": str(self.project), "project": "project", "memory": [str(self.memory)]}],
            }),
            encoding="utf-8",
        )
        (self.memory / "source.md").write_text(
            "# Source\n\nA source note.\n\n[Target](target.md#part)\n",
            encoding="utf-8",
        )
        (self.memory / "target.md").write_text("# Target\n\nA target note.\n", encoding="utf-8")
        settings = am.load_settings(self.settings)
        conn = am.connect_db(am.database_path(settings, self.settings))
        try:
            am.sync_index(conn, am.collect_memory_roots(settings, self.settings))
        finally:
            conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = am.main(["--settings", str(self.settings), *args])
        return code, output.getvalue()

    def test_default_output_is_yaml(self):
        code, output = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("settings:", output)
        self.assertIn("documents: 2", output)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(output)

        # Keep a parser-independent fallback when PyYAML is not installed: this
        # verifies nested mapping/list indentation rather than the old prose output.
        code, output = self.run_cli("resolve", "--path", str(self.project))
        self.assertEqual(code, 0)
        self.assertIn("memory:\n  - path:", output)
        self.assertIn("    tags: []", output)

    def test_json_output_remains_machine_compatible(self):
        code, output = self.run_cli("--json", "status")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["documents"], 2)
        self.assertEqual(payload["links"], 1)

    def test_navigation_briefs_are_pruned_before_candidates_and_remain_bounded(self):
        payload = {
            "status": "ok",
            "project": "project",
            "source": "path",
            "scope": {"project": "project", "source": "path", "include_global": True, "tags": []},
            "counts": {"distinct": 1, "global": 1, "project": 0, "components_overlap": True},
            "tags": [],
            "navigation": [{
                "id": 4,
                "scope": "_shared",
                "title": "Shared route",
                "brief": "oversized selected brief " * 500,
                "path": "/memory/shared.md",
            }],
            "project_summaries": [],
            "configured_projects": [],
            "omitted": 0,
            "next_commands": ["agent-memory context --project project"],
            "diagnostics": [],
            "truncated": False,
        }

        json_output = bounded_json(payload, budget=512)
        text_output = bounded_navigation_text(payload, budget=512)
        structured = json.loads(json_output)

        self.assertLessEqual(len(json_output.encode("utf-8")), 512)
        self.assertLessEqual(len(text_output.encode("utf-8")), 512)
        self.assertEqual(structured["navigation"][0]["id"], 4)
        self.assertEqual(structured["omitted"], 0)
        self.assertLess(len(structured["navigation"][0].get("brief", "")), 100)
        self.assertIn("Shared route", text_output)
        self.assertIn("omitted: 0", text_output)

    def test_init_and_doctor_use_the_shared_formatter(self):
        fresh = self.root / "fresh-settings.json"
        output = io.StringIO()
        with redirect_stdout(output):
            code = am.main(["--settings", str(fresh), "init"])
        self.assertEqual(code, 0)
        self.assertIn("settings:", output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output):
            code = am.main(["--settings", str(fresh), "--table", "init", "--force"])
        self.assertEqual(code, 0)
        self.assertIn("field", output.getvalue())
        self.assertIn("settings", output.getvalue())

        code, output = self.run_cli("--table", "doctor")
        self.assertEqual(code, 0)
        self.assertIn("status: ok", output)
        self.assertIn("Findings", output)
        self.assertIn("severity", output)

    def test_tables_have_collection_specific_layouts(self):
        code, output = self.run_cli("--table", "list")
        self.assertEqual(code, 0)
        self.assertIn("┌", output)
        self.assertIn("title", output)
        self.assertIn("Source", output)

        code, output = self.run_cli("--table", "links", str(self.memory / "source.md"))
        self.assertEqual(code, 0)
        self.assertIn("Outbound", output)
        self.assertIn("Inbound", output)
        self.assertIn("Target", output)
        self.assertIn("ok", output)

    def test_text_links_keep_human_readable_sections(self):
        code, output = self.run_cli("--text", "links", str(self.memory / "source.md"))
        self.assertEqual(code, 0)
        self.assertIn("outbound:", output)
        self.assertIn("inbound:", output)
        self.assertIn("[ok]", output)

    def test_output_flags_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            am.build_parser().parse_args(["--json", "--table", "status"])
        with self.assertRaises(SystemExit):
            self.run_cli("--table", "tags", "--json")

    def test_output_flags_work_before_or_after_the_subcommand(self):
        cases = [
            ("tags", "--table"),
            ("search", "Source", "--table"),
            ("--table", "tags"),
        ]
        for args in cases:
            with self.subTest(args=args):
                code, output = self.run_cli(*args)
                self.assertEqual(code, 0)
                self.assertIn("┌", output)

    def test_yaml_round_trip_when_pyyaml_is_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is optional; skipping parser round-trip")
        value = {
            "status": "yes",
            "count": 3,
            "enabled": True,
            "missing": None,
            "items": [{"name": "one", "values": ["2", False]}],
        }
        parsed = yaml.safe_load(yaml_dump(value))
        self.assertEqual(parsed, value)


if __name__ == "__main__":
    unittest.main()
