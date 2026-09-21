import importlib.util
import io
import json
import os
import shlex
import subprocess
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
            content = (
                f"Shared guide {index}."
                if index < 3
                else f"PRIVATE project guidance {index}."
            )
            (target / f"note-{index}.md").write_text(
                f"---\ntitle: Note {index}\ntags: [tag-{index}, common]\n---\n\n{content}\n", encoding="utf-8"
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

    def test_scope_priority_and_context_change_brief_selection(self):
        (self.memory / "note-context.md").write_text(
            "---\ntitle: Payments rollout\ntags: [payments]\n---\n\nBranch-specific guidance.\n",
            encoding="utf-8",
        )
        settings = am.load_settings(self.settings_path)
        conn = am.connect_db(am.database_path(settings, self.settings_path))
        am.sync_index(conn, am.collect_memory_roots(settings, self.settings_path))
        conn.close()

        with patch.object(am, "current_context_terms", return_value=("payments", "rollout")):
            code, out, _ = self.cli("--json", "brief", "--path", str(self.project))
        self.assertEqual(code, 0)
        scoped = json.loads(out)
        self.assertEqual(scoped["navigation"][0]["scope"], "demo")
        self.assertEqual(scoped["navigation"][0]["title"], "Payments rollout")
        self.assertEqual(scoped["navigation"][0]["brief"], "Branch-specific guidance.")
        with patch.object(am, "current_context_terms", return_value=("payments", "rollout")):
            text_code, project_text, _ = self.cli("brief", "--path", str(self.project))
        self.assertEqual(text_code, 0)
        self.assertIn("brief: Branch-specific guidance.", project_text)

        code, out, _ = self.cli("--json", "brief", "--path", str(self.root))
        self.assertEqual(code, 0)
        unregistered = json.loads(out)
        self.assertEqual(unregistered["navigation"][0]["scope"], "_shared")
        self.assertTrue(unregistered["navigation"][0]["brief"])
        self.assertNotEqual(scoped["navigation"], unregistered["navigation"])
        self.assertEqual(unregistered["project_summaries"], [{"project": "demo", "count": 6}])
        self.assertEqual(
            set(unregistered["project_summaries"][0]), {"project", "count"}
        )
        rendered = json.dumps(unregistered)
        self.assertNotIn("PRIVATE project guidance", rendered)
        self.assertNotIn("Note 3", rendered)
        self.assertNotIn(str(self.memory), rendered)
        self.assertLessEqual(len(out.encode()), 2048)
        text_code, shared_text, _ = self.cli("brief", "--path", str(self.root))
        self.assertEqual(text_code, 0)
        self.assertIn(f"brief: {unregistered['navigation'][0]['brief']}", shared_text)

    def test_unregistered_metadata_overlap_changes_selection_without_changing_scope_or_hits(self):
        for label in ("Payments", "Deployment"):
            for suffix in ("A", "B"):
                (self.shared / f"note-guide-{label.lower()}-{suffix}.md").write_text(
                    f"---\ntitle: {label} Playbook {suffix}\nbrief: Follow the {label.lower()} checklist.\ntags: [operations]\n---\n\nPrivate body text.\n",
                    encoding="utf-8",
                )
        settings = am.load_settings(self.settings_path)
        conn = am.connect_db(am.database_path(settings, self.settings_path))
        am.sync_index(conn, am.collect_memory_roots(settings, self.settings_path))
        conn.close()
        path_a = self.root / "payments"
        path_b = self.root / "deployment"
        path_a.mkdir()
        path_b.mkdir()

        code, out_a, _ = self.cli("--json", "brief", "--path", str(path_a))
        code_b, out_b, _ = self.cli("--json", "brief", "--path", str(path_b))
        brief_a, brief_b = json.loads(out_a), json.loads(out_b)
        self.assertEqual((code, code_b), (0, 0))
        self.assertEqual(brief_a["status"], "unregistered")
        self.assertEqual(brief_b["status"], "unregistered")
        self.assertEqual(brief_a["scope"], brief_b["scope"])
        self.assertEqual(brief_a["counts"], brief_b["counts"])
        self.assertEqual(brief_a["project_summaries"], brief_b["project_summaries"])
        self.assertEqual(
            {item["title"] for item in brief_a["navigation"]},
            {"Payments Playbook A", "Payments Playbook B"},
        )
        self.assertEqual(
            {item["title"] for item in brief_b["navigation"]},
            {"Deployment Playbook A", "Deployment Playbook B"},
        )
        self.assertTrue(all(item["brief"] for item in brief_a["navigation"]))
        self.assertTrue(all(item["brief"] for item in brief_b["navigation"]))

        _, hits_a, _ = self.cli("--json", "search", "Note 0", "--path", str(path_a))
        _, hits_b, _ = self.cli("--json", "search", "Note 0", "--path", str(path_b))
        self.assertEqual(json.loads(hits_a), json.loads(hits_b))

    def test_context_terms_use_only_bounded_path_git_and_filename_metadata(self):
        repo = self.root / "context-repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "--initial-branch=feature/payments"], check=True, capture_output=True)
        source = repo / "src" / "payments_rollout.py"
        source.parent.mkdir()
        source.write_text("initial", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "src/payments_rollout.py"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial"],
            check=True,
            capture_output=True,
        )
        source.write_text("SensitiveBodyMustNotBecomeContext", encoding="utf-8")

        terms = am.current_context_terms(repo)

        self.assertLessEqual(len(terms), 24)
        self.assertTrue({"context", "repo", "payments", "rollout"}.issubset(terms))
        self.assertNotIn("sensitivebodymustnotbecomecontext", terms)

    def test_registration_suggestion_is_executable_and_settings_write_is_explicit(self):
        task = self.root / "unregistered-task"
        learnings = task / ".learnings"
        learnings.mkdir(parents=True)
        before = self.settings_path.read_bytes()

        code, out, _ = self.cli("--json", "brief", "--path", str(task))
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(self.settings_path.read_bytes(), before)
        self.assertIn("registration_suggestion", [item["code"] for item in payload["diagnostics"]])
        command = payload["next_commands"][0]
        self.assertIn(" register ", f" {command} ")

        argv = shlex.split(command)
        self.assertEqual(argv[0], "agent-memory")
        register_out, register_err = io.StringIO(), io.StringIO()
        with redirect_stdout(register_out), redirect_stderr(register_err):
            result = am.main(argv[1:])
        self.assertEqual(result, 0)
        self.assertEqual(register_err.getvalue(), "")
        updated = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["bindings"][-1]["project"], task.name)
        self.assertEqual(updated["bindings"][-1]["memory"], [str(learnings)])
        self.assertFalse(list(self.root.glob(".settings.json.*.tmp")))

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
