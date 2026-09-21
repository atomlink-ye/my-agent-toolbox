import importlib.util
import io
import json
import os
import shlex
import sqlite3
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
                f"---\ntitle: Note {index}\nstatus: validated\ntags: [tag-{index}, common]\n---\n\n{content}\n", encoding="utf-8"
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

    def sync(self):
        settings = am.load_settings(self.settings_path)
        conn = am.connect_db(am.database_path(settings, self.settings_path))
        try:
            return am.sync_index(conn, am.collect_memory_roots(settings, self.settings_path))
        finally:
            conn.close()

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

    def test_brief_classifies_mixed_metadata_conservatively(self):
        notes = {
            "explicit-core": ("type: project\nstatus: raw\ntier: core", "Explicit core"),
            "explicit-archive": ("type: feedback\nstatus: validated\ntier: archive", "Explicit archive"),
            "validated": ("type: project\nstatus: validated", "Validated"),
            "promoted": ("type: project\nstatus: promoted", "Promoted"),
            "raw-user": ("type: user\nstatus: raw", "Raw user"),
            "superseded-feedback": ("type: feedback\nstatus: superseded", "Superseded feedback"),
            "user": ("type: user", "User preference"),
            "feedback": ("type: feedback", "Feedback preference"),
            "project-default": ("type: project", "Project default"),
            "metadata-default": ("", "Metadata default"),
        }
        for filename, (metadata, title) in notes.items():
            (self.memory / f"{filename}.md").write_text(
                f"---\ntitle: {title}\n{metadata}\n---\n\n{title} body.\n",
                encoding="utf-8",
            )
        settings = am.load_settings(self.settings_path)
        conn = am.connect_db(am.database_path(settings, self.settings_path))
        am.sync_index(conn, am.collect_memory_roots(settings, self.settings_path))
        conn.close()

        with patch.object(am, "current_context_terms", return_value=("explicit", "core")):
            code, out, _ = self.cli("--json", "brief", "--path", str(self.project))
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["counts"]["distinct"], 18)
        self.assertEqual(payload["counts"]["core"], 13)
        self.assertEqual(payload["counts"]["archive"], 5)
        self.assertEqual(payload["counts"]["defaulted"], 2)
        shown = {row["title"] for row in payload["navigation"]}
        self.assertTrue(shown)
        self.assertTrue(shown.isdisjoint({
            "Explicit archive", "Raw user", "Superseded feedback",
            "Project default", "Metadata default",
        }))
        self.assertIn("Explicit core", shown)
        default = next(item for item in payload["diagnostics"] if item["code"] == "tier_default_archive")
        self.assertEqual(default["count"], 2)

    def test_context_expands_all_tiers_beyond_brief_candidates(self):
        (self.memory / "archive.md").write_text(
            "---\ntitle: Archived evidence\nstatus: raw\n---\n\nHistorical event.\n",
            encoding="utf-8",
        )
        settings = am.load_settings(self.settings_path)
        conn = am.connect_db(am.database_path(settings, self.settings_path))
        am.sync_index(conn, am.collect_memory_roots(settings, self.settings_path))
        conn.close()

        brief_code, brief_out, _ = self.cli("--json", "brief", "--path", str(self.project))
        context_code, context_out, _ = self.cli("--json", "context", "--path", str(self.project))
        brief, context = json.loads(brief_out), json.loads(context_out)
        self.assertEqual((brief_code, context_code), (0, 0))
        self.assertEqual(brief["counts"]["core"], 8)
        self.assertEqual(brief["counts"]["archive"], 1)
        self.assertNotIn("Archived evidence", {row["title"] for row in brief["navigation"]})
        self.assertIn("Archived evidence", {row["title"] for row in context["navigation"]})
        self.assertEqual(context["counts"]["core"], 8)
        self.assertEqual(context["counts"]["archive"], 1)
        self.assertGreater(len(context["navigation"]), len(brief["navigation"]))
        self.assertLessEqual(len(brief_out.encode("utf-8")), 2048)
        self.assertLessEqual(len(context_out.encode("utf-8")), 8192)

    def test_context_has_larger_budget_for_all_tier_expansion(self):
        long_brief = "Archive routing detail. " * 10
        for index in range(24):
            (self.memory / f"archive-{index:02d}.md").write_text(
                f"---\ntitle: Archive topic {index:02d}\nstatus: raw\nbrief: {long_brief}\n---\n\nEvidence.\n",
                encoding="utf-8",
            )
        self.sync()
        brief_code, brief_out, _ = self.cli("--json", "brief", "--path", str(self.project))
        context_code, context_out, _ = self.cli("--json", "context", "--path", str(self.project))
        self.assertEqual((brief_code, context_code), (0, 0))
        self.assertGreater(len(context_out.encode("utf-8")), len(brief_out.encode("utf-8")))
        self.assertGreater(len(json.loads(context_out)["navigation"]), len(json.loads(brief_out)["navigation"]))
        self.assertLessEqual(len(brief_out.encode("utf-8")), 2048)
        self.assertLessEqual(len(context_out.encode("utf-8")), 8192)

    def test_explicit_tier_is_indexed_and_legacy_readonly_index_infers(self):
        (self.memory / "tier-override.md").write_text(
            "---\ntitle: Explicit core override\nstatus: raw\ntier: core\n---\n\nOverride.\n",
            encoding="utf-8",
        )
        self.sync()
        database = am.database_path(am.load_settings(self.settings_path), self.settings_path)
        with sqlite3.connect(database) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_meta)")}
            self.assertIn("tier", columns)
            self.assertEqual(conn.execute("SELECT tier FROM memory_meta WHERE tier='core'").fetchone()[0], "core")

        with patch.object(am, "current_context_terms", return_value=("explicit", "core", "override")):
            code, out, _ = self.cli("--json", "brief", "--path", str(self.project))
        self.assertEqual(code, 0)
        self.assertIn("Explicit core override", {row["title"] for row in json.loads(out)["navigation"]})

        with sqlite3.connect(database) as conn:
            conn.execute("ALTER TABLE memory_meta DROP COLUMN tier")
        code, out, err = self.cli("--json", "brief", "--path", str(self.project))
        payload = json.loads(out)
        self.assertEqual(code, 0, err)
        self.assertNotIn("Explicit core override", {row["title"] for row in payload["navigation"]})
        self.assertEqual(payload["counts"]["core"], 8)
        self.assertEqual(payload["counts"]["archive"], 1)

    def test_project_shared_overlap_deduplicates_and_backfills_tagged_routes(self):
        for index in range(5, 8):
            (self.memory / f"extra-{index}.md").write_text(
                f"---\ntitle: Extra core {index}\nstatus: validated\ntags: [common]\n---\n\nExtra.\n",
                encoding="utf-8",
            )
        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        settings["shared"] = [str(self.memory)]
        self.settings_path.write_text(json.dumps(settings), encoding="utf-8")
        self.sync()

        brief_code, brief_out, _ = self.cli("--json", "brief", "--path", str(self.project))
        brief = json.loads(brief_out)
        shown = brief["navigation"]
        shown_ids = [row["id"] for row in shown]
        self.assertEqual(brief_code, 0)
        self.assertEqual(brief["counts"]["distinct"], 8)
        self.assertEqual(brief["counts"]["core"] + brief["counts"]["archive"], 8)
        self.assertEqual(len(shown), 6)
        self.assertEqual(len(set(shown_ids)), 6)
        self.assertEqual([row["scope"] for row in shown[:4]], ["demo"] * 4)
        self.assertEqual([row["scope"] for row in shown[4:]], ["_shared"] * 2)
        self.assertEqual(brief["omitted"], brief["counts"]["distinct"] - len(set(shown_ids)))

        context_code, context_out, _ = self.cli(
            "--json", "context", "--path", str(self.project), "--tag", "common"
        )
        context = json.loads(context_out)
        context_ids = [row["id"] for row in context["navigation"]]
        self.assertEqual(context_code, 0)
        self.assertEqual(context["counts"]["distinct"], 8)
        self.assertEqual(context["counts"]["core"] + context["counts"]["archive"], 8)
        self.assertEqual(len(context_ids), len(set(context_ids)))
        self.assertEqual(len(context_ids), 8)

    def test_empty_core_brief_gives_context_and_lifecycle_commands(self):
        for target in (self.memory, self.shared):
            for note in target.glob("*.md"):
                text = note.read_text(encoding="utf-8")
                if text.startswith("---\n"):
                    end = text.find("\n---\n", 4)
                    frontmatter = text[4:end]
                    lines = [line for line in frontmatter.splitlines() if not line.startswith("status:")]
                    note.write_text(
                        "---\n" + "\n".join(lines) + "\nstatus: raw\n---\n" + text[end + 5 :],
                        encoding="utf-8",
                    )
        settings = am.load_settings(self.settings_path)
        conn = am.connect_db(am.database_path(settings, self.settings_path))
        am.sync_index(conn, am.collect_memory_roots(settings, self.settings_path))
        conn.close()

        code, out, _ = self.cli("--json", "brief", "--path", str(self.project))
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["counts"]["core"], 0)
        self.assertEqual(payload["counts"]["archive"], 8)
        self.assertEqual(payload["navigation"], [])
        self.assertIn("core", payload["tier_guidance"].lower())
        self.assertIn("context --project demo", payload["next_commands"][0])
        lifecycle = payload["next_commands"][1]
        lifecycle_args = shlex.split(lifecycle)
        self.assertEqual(lifecycle_args[0:2], ["agent-memory", "lifecycle"])
        self.assertTrue(lifecycle_args[2].isdigit() or lifecycle_args[2].startswith("mem_"))
        self.assertEqual(lifecycle_args[3:], ["--tier", "core"])

        text_code, text_out, _ = self.cli("brief", "--path", str(self.project))
        self.assertEqual(text_code, 0)
        self.assertIn("core=0 archive=8", text_out)
        self.assertIn("tier is empty", text_out.lower())
        self.assertIn("agent-memory lifecycle", text_out)
        self.assertLessEqual(len(text_out.encode("utf-8")), 2048)

        lifecycle_code, lifecycle_out, lifecycle_err = self.cli("--json", *lifecycle_args[1:])
        self.assertEqual(lifecycle_code, 0, lifecycle_err)
        self.assertIn('"tier":"core"', lifecycle_out)

    def test_unregistered_archive_keeps_registration_and_tier_route(self):
        for note in self.shared.glob("*.md"):
            note.write_text(
                note.read_text(encoding="utf-8").replace("status: validated", "status: raw"),
                encoding="utf-8",
            )
        self.sync()
        task = self.root / "unregistered-archive-task"
        (task / ".learnings").mkdir(parents=True)
        code, out, _ = self.cli("--json", "brief", "--path", str(task))
        payload = json.loads(out)
        commands = payload["next_commands"]
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "unregistered")
        self.assertEqual(payload["counts"]["core"], 0)
        self.assertEqual(payload["counts"]["archive"], 3)
        self.assertIn(" register ", f" {commands[0]} ")
        self.assertTrue(any("agent-memory context" in command for command in commands))
        self.assertTrue(any("--tier core" in command for command in commands))

    def test_scope_priority_and_context_change_brief_selection(self):
        (self.memory / "note-context.md").write_text(
            "---\ntitle: Payments rollout\nstatus: validated\ntags: [payments]\n---\n\nBranch-specific guidance.\n",
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
                    f"---\ntitle: {label} Playbook {suffix}\nstatus: validated\nbrief: Follow the {label.lower()} checklist.\ntags: [operations]\n---\n\nPrivate body text.\n",
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
        code, out, _ = self.cli("--json", "brief", "--path", str(self.project))
        registered_empty = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(registered_empty["status"], "empty")
        self.assertTrue(any("agent-memory context --project demo" in command for command in registered_empty["next_commands"]))
        self.assertTrue(any("agent-memory capture" in command for command in registered_empty["next_commands"]))
        self.assertNotIn("NOTE_PATH", json.dumps(registered_empty))

        task = self.root / "unregistered-empty"
        (task / ".learnings").mkdir(parents=True)
        code, out, _ = self.cli("--json", "brief", "--path", str(task))
        unregistered_empty = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(unregistered_empty["status"], "unregistered")
        self.assertEqual(unregistered_empty["counts"]["distinct"], 0)
        self.assertIn(" register ", f" {unregistered_empty['next_commands'][0]} ")
        self.assertNotIn("NOTE_PATH", json.dumps(unregistered_empty))


if __name__ == "__main__":
    unittest.main()
