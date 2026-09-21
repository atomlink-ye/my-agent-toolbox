import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[3] / "skills" / "agent-memory" / "scripts" / "agent_memory.py"


class TierCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.memory = self.root / "memory"
        self.shared = self.root / "shared"
        for path in (self.project, self.memory, self.shared):
            path.mkdir()
        self.settings = self.root / "settings.json"
        self.settings.write_text(
            json.dumps(
                {
                    "version": 1,
                    "database": str(self.root / "index.sqlite3"),
                    "shared": [str(self.shared)],
                    "bindings": [
                        {
                            "path": str(self.project),
                            "project": "demo",
                            "memory": [str(self.memory)],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self._stamp = 1_700_000_000_000_000_000
        self.add_note(
            "explicit-core.md",
            "Explicit Core",
            ("type: project", "status: raw", "tier: core"),
        )
        self.add_note(
            "explicit-archive.md",
            "Explicit Archive",
            ("type: feedback", "status: validated", "tier: archive"),
        )
        self.add_note(
            "status-core.md",
            "Status Core",
            ("type: project", "status: validated"),
        )
        self.add_note(
            "status-archive.md",
            "Status Archive",
            ("type: feedback", "status: raw"),
        )
        self.add_note(
            "type-core.md",
            "Type Core",
            ("type: user", "status: custom"),
        )
        self.add_note(
            "default-archive.md",
            "Default Archive",
            ("type: project", "status: custom"),
        )
        nested = self.memory / "nested-feedback.md"
        nested.write_text(
            "---\n"
            "title: Nested Feedback\n"
            "brief: Nested Feedback\n"
            "metadata:\n"
            "  type: feedback\n"
            "tags: [marked]\n"
            "---\n\nFixture note.\n",
            encoding="utf-8",
        )
        os.utime(nested, ns=(self._stamp, self._stamp))
        self._stamp += 1_000_000
        descendant = self.memory / "nested-audit-feedback.md"
        descendant.write_text(
            "---\n"
            "title: Nested Audit Feedback\n"
            "brief: Nested Audit Feedback\n"
            "metadata:\n"
            "  audit:\n"
            "    type: feedback\n"
            "tags: [marked]\n"
            "---\n\nFixture note.\n",
            encoding="utf-8",
        )
        os.utime(descendant, ns=(self._stamp, self._stamp))
        self._stamp += 1_000_000
        code, _, err = self.cli("sync")
        self.assertEqual((code, err), (0, ""))

    def tearDown(self):
        self.temp.cleanup()

    def add_note(self, filename, title, fields, tags=("marked",)):
        path = self.memory / filename
        frontmatter = [
            f"title: {title}",
            f"brief: {title}",
            *fields,
            "tags: [" + ", ".join(tags) + "]",
        ]
        path.write_text(
            "---\n" + "\n".join(frontmatter) + "\n---\n\nFixture note.\n",
            encoding="utf-8",
        )
        os.utime(path, ns=(self._stamp, self._stamp))
        self._stamp += 1_000_000
        return path

    def cli(self, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--settings", str(self.settings), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr

    def json_cli(self, *args):
        code, out, err = self.cli(*args)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def test_verbose_brief_exposes_tier_and_truthful_precedence(self):
        code, out, err = self.cli(
            "--json", "--verbose", "brief", "--path", str(self.project)
        )
        self.assertEqual((code, err), (0, ""))
        self.assertLessEqual(len(out.encode("utf-8")), 2048)
        payload = json.loads(out)
        self.assertEqual(payload["counts"]["distinct"], 8)
        self.assertEqual(payload["counts"]["core"], 4)
        self.assertEqual(payload["counts"]["archive"], 4)
        self.assertEqual(payload["counts"]["defaulted"], 2)
        rows = payload["navigation"]
        self.assertTrue(rows)
        self.assertTrue(
            all("tier" in row and "tier_source" in row for row in rows)
        )
        self.assertTrue(all(row["tier"] == "core" for row in rows))

        context = self.json_cli(
            "--json", "--verbose", "context", "--project", "demo"
        )
        by_title = {row["title"]: row for row in context["navigation"]}
        self.assertEqual(
            {title: (by_title[title]["tier"], by_title[title]["tier_source"])
             for title in by_title},
            {
                "Explicit Core": ("core", "explicit"),
                "Explicit Archive": ("archive", "explicit"),
                "Status Core": ("core", "status"),
                "Status Archive": ("archive", "status"),
                "Type Core": ("core", "type"),
                "Default Archive": ("archive", "default"),
                "Nested Feedback": ("core", "type"),
                "Nested Audit Feedback": ("archive", "default"),
            },
        )

    def test_list_tier_filters_compose_and_run_before_limit(self):
        for index in range(25):
            self.add_note(
                f"core-{index:02d}.md",
                f"Additional Core {index:02d}",
                ("type: project", "status: validated"),
                ("marked", "many-core"),
            )
        for index in range(25):
            self.add_note(
                f"archive-{index:02d}.md",
                f"Additional Archive {index:02d}",
                ("type: project", "status: custom"),
                ("marked", "many-archive"),
            )
        self.cli("sync")

        eligible = self.json_cli(
            "list", "--json", "--verbose", "--project", "demo", "--tag", "marked",
            "--tier", "core", "--limit", "100",
        )
        limited = self.json_cli(
            "list", "--json", "--verbose", "--project", "demo", "--tag", "marked",
            "--tier", "core", "--limit", "20",
        )
        self.assertGreater(len(eligible), 20)
        self.assertEqual(len(limited), min(20, len(eligible)))
        self.assertTrue(all(row["tier"] == "core" for row in limited))
        archive_eligible = self.json_cli(
            "list", "--json", "--verbose", "--project", "demo", "--tag", "marked",
            "--tier", "archive", "--limit", "100",
        )
        archive_limited = self.json_cli(
            "list", "--json", "--verbose", "--project", "demo", "--tag", "marked",
            "--tier", "archive", "--limit", "20",
        )
        self.assertGreater(len(archive_eligible), 20)
        self.assertEqual(len(archive_limited), min(20, len(archive_eligible)))
        self.assertTrue(all(row["tier"] == "archive" for row in archive_limited))
        one = self.json_cli(
            "list", "--json", "--verbose", "--project", "demo", "--tag", "marked",
            "--tier", "core", "--limit", "1",
        )
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["tier"], "core")

    def test_context_tier_filters_precede_sampling_and_filter_inventory_counts(self):
        for index in range(16):
            self.add_note(
                f"archive-{index:02d}.md",
                f"Additional Archive {index:02d}",
                ("type: project", "status: custom"),
                ("many-archive",),
            )
        self.cli("sync")

        core = self.json_cli(
            "--json", "--verbose", "context", "--project", "demo",
            "--tag", "marked", "--tier", "core",
        )
        self.assertEqual(core["counts"]["distinct"], 4)
        self.assertEqual(core["counts"]["core"], 4)
        self.assertEqual(core["counts"]["archive"], 0)
        self.assertEqual(core["omitted"], 0)
        self.assertEqual(
            {row["title"] for row in core["navigation"]},
            {"Explicit Core", "Status Core", "Type Core", "Nested Feedback"},
        )
        empty = self.json_cli(
            "--json", "--verbose", "context", "--project", "demo",
            "--tag", "absent", "--tier", "core",
        )
        self.assertEqual(empty["status"], "empty")
        self.assertEqual(empty["scope"]["project"], "demo")
        self.assertEqual(empty["counts"]["distinct"], 0)
        self.assertEqual(empty["counts"]["project"], 0)
        self.assertEqual(empty["navigation"], [])
        self.assertEqual(empty["omitted"], 0)

        archive = self.json_cli(
            "--json", "--verbose", "context", "--project", "demo",
            "--tier", "archive",
        )
        self.assertEqual(archive["counts"]["distinct"], 20)
        self.assertEqual(archive["counts"]["archive"], 20)
        self.assertEqual(archive["counts"]["core"], 0)
        self.assertEqual(len(archive["navigation"]), 15)
        self.assertEqual(archive["omitted"], 5)
        self.assertTrue(all(row["tier"] == "archive" for row in archive["navigation"]))

    def test_ordinary_json_and_compact_keep_audit_fields_out(self):
        ordinary = self.json_cli("list", "--json", "--project", "demo", "--limit", "20")
        self.assertTrue(ordinary)
        self.assertTrue(
            all("tier" not in row and "tier_source" not in row for row in ordinary)
        )
        ordinary_context = self.json_cli(
            "--json", "context", "--project", "demo"
        )
        self.assertTrue(
            all(
                "tier" not in row and "tier_source" not in row
                for row in ordinary_context["navigation"]
            )
        )

        code, compact, err = self.cli(
            "list", "--compact", "--project", "demo", "--limit", "2"
        )
        self.assertEqual((code, err), (0, ""))
        expected = (
            f"Read paths relative to {self.memory}:\n"
            "1. Nested Audit Feedback\n   nested-audit-feedback.md\n"
            "2. Nested Feedback\n   nested-feedback.md\n"
        )
        self.assertEqual(compact, expected)
        self.assertEqual(len(compact.encode("utf-8")), len(expected.encode("utf-8")))
        self.assertNotIn("tier", compact)
        self.assertNotIn("tier_source", compact)

    def test_capture_tier_is_explicit_and_omission_keeps_auto_behavior(self):
        explicit_summary = "P4 test explicit core capture marker 94731"
        explicit = self.json_cli(
            "capture", "learning", explicit_summary, "--project", "demo",
            "--details", "Unique P4 verification content for explicit core capture.",
            "--tag", "p4-test:explicit", "--tier", "core", "--allow-duplicate",
            "--json",
        )
        self.assertTrue(explicit["created"])
        self.assertEqual(explicit["tier"], "core")
        explicit_path = Path(explicit["path"])
        self.assertIn("tier: core", explicit_path.read_text(encoding="utf-8"))
        brief = self.json_cli(
            "--json", "--verbose", "brief", "--path", str(self.project)
        )
        captured = next(
            row for row in brief["navigation"] if row["title"] == explicit_summary
        )
        self.assertEqual((captured["tier"], captured["tier_source"]), ("core", "explicit"))

        omitted_summary = "P4 test omitted tier auto archive marker 58264"
        omitted = self.json_cli(
            "capture", "learning", omitted_summary, "--project", "demo",
            "--details", "Unique P4 verification content for omitted tier behavior.",
            "--tag", "p4-test:omitted", "--allow-duplicate", "--json",
        )
        self.assertTrue(omitted["created"])
        self.assertEqual(omitted["status"], "raw")
        self.assertNotIn("tier", omitted)
        omitted_text = Path(omitted["path"]).read_text(encoding="utf-8")
        self.assertNotIn("\ntier:", omitted_text)
        context = self.json_cli(
            "--json", "--verbose", "context", "--project", "demo", "--tier", "archive"
        )
        auto = next(
            row for row in context["navigation"] if row["title"] == omitted_summary
        )
        self.assertEqual((auto["tier"], auto["tier_source"]), ("archive", "status"))


if __name__ == "__main__":
    unittest.main()
