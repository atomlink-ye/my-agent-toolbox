import hashlib
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).parents[3]
SKILL = REPO / "skills" / "agent-memory" / "SKILL.md"
DOC = REPO / "docs" / "agent-memory.md"
OPENING_QUERIES = REPO / "eval" / "agent-memory" / "evals" / "opening-queries.json"


FIXTURE_CLI = r'''#!/usr/bin/env python3
import argparse
import json
import os
import stat
import sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--settings", required=True)
p.add_argument("--json", action="store_true")
s = p.add_subparsers(dest="command", required=True)
for command in ("brief", "context"):
    q = s.add_parser(command)
    q.add_argument("--path")
q = s.add_parser("search")
q.add_argument("query")
q.add_argument("--path")
q.add_argument("--limit", type=int, default=10)
a = p.parse_args()
settings_path = Path(a.settings)
if not (settings_path.stat().st_mode & stat.S_IRUSR):
    print(f"agent-memory: permission denied reading settings: {settings_path}", file=sys.stderr)
    raise SystemExit(2)
settings = json.loads(settings_path.read_text(encoding="utf-8"))
scope = settings["scope"]
budget = settings["budget"]
base = {"scope": scope, "budget": budget, "settings": str(settings_path.resolve()),
        "cwd": str(Path.cwd().resolve())}
if a.command == "brief":
    payload = {**base, "count": 2, "index": {"available": True, "documents": 2},
               "tags": [{"tag": "workflow:review", "count": 1}],
               "hooks": [{"topic": "review", "path": "memory/review.md"}],
               "navigation": {"next_commands": [["context", "--path", a.path or str(Path.cwd())]]}}
elif a.command == "context":
    payload = {**base, "routes": [{"topic": "review", "path": "memory/review.md"}],
               "navigation": {"next_commands": [["search", "review", "--path", a.path or str(Path.cwd()), "--limit", str(budget)]]}}
else:
    if a.limit > budget:
        print(f"agent-memory: requested limit {a.limit} exceeds budget {budget}", file=sys.stderr)
        raise SystemExit(2)
    payload = {**base, "query": a.query, "results": [], "reason": "no_match",
               "navigation": {"next_commands": [["context", "--path", a.path or str(Path.cwd())]]}}
print(json.dumps(payload, ensure_ascii=False))
'''


def snapshot_tree(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


class AgentMemoryArchitectureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "workspace" / "project-a"
        self.project.mkdir(parents=True)
        (self.project / "memory").mkdir()
        (self.project / "memory" / "review.md").write_text("# Review\n", encoding="utf-8")
        self.settings = self.root / "settings.json"
        self.settings.write_text(json.dumps({"scope": "project-a", "budget": 4}), encoding="utf-8")
        self.cli = self.root / "fixture_agent_memory.py"
        self.cli.write_text(FIXTURE_CLI, encoding="utf-8")
        self.cli.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, args, *, settings=None, expected=0):
        command = [sys.executable, str(self.cli), "--settings", str(settings or self.settings), "--json", *args]
        completed = subprocess.run(command, cwd=self.project, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, expected, completed.stderr)
        return completed

    def assert_scope_and_budget(self, payload):
        self.assertEqual(payload["scope"], "project-a")
        self.assertEqual(payload["budget"], 4)
        self.assertEqual(payload["cwd"], str(self.project.resolve()))
        self.assertEqual(payload["settings"], str(self.settings.resolve()))

    def run_next_commands(self, payload):
        commands = payload["navigation"]["next_commands"]
        self.assertTrue(commands)
        for command in commands:
            result = self.run_cli(command)
            self.assert_scope_and_budget(json.loads(result.stdout))
        return len(commands)

    def test_brief_first_and_zero_match_navigation_commands_execute(self):
        brief = json.loads(self.run_cli(["brief"]).stdout)
        self.assert_scope_and_budget(brief)
        self.assertEqual(brief["count"], 2)
        self.assertTrue(brief["hooks"])
        self.run_next_commands(brief)

        no_match = json.loads(self.run_cli(["search", "conventions", "--limit", "4"]).stdout)
        self.assert_scope_and_budget(no_match)
        self.assertEqual(no_match["results"], [])
        self.assertEqual(no_match["reason"], "no_match")
        self.run_next_commands(no_match)

        context = json.loads(self.run_cli(["context"]).stdout)
        self.assert_scope_and_budget(context)
        self.run_next_commands(context)

    def test_opening_query_acceptance_and_synthetic_all_miss_fixture(self):
        spec = json.loads(OPENING_QUERIES.read_text(encoding="utf-8"))
        self.assertEqual(
            [case["query"] for case in spec["cases"]],
            ["memory", "how do I start", "conventions", "what should I know", "setup", "记忆", "best practices", "gotchas"],
        )
        self.assertEqual(spec["metrics"], {"actionable": "8/8", "dead_end": 0})
        self.assertIn("environment", spec["provenance"])
        self.assertIn("observed_hit_count", spec["cases"][0])
        actionable = 0
        dead_end = 0
        for case in spec["cases"]:
            payload = json.loads(self.run_cli(["search", case["query"], "--limit", "4"]).stdout)
            self.assertEqual(payload["results"], [])
            if payload["navigation"]["next_commands"]:
                self.run_next_commands(payload)
                actionable += 1
            else:
                dead_end += 1
        self.assertEqual(f"{actionable}/{len(spec['cases'])}", spec["metrics"]["actionable"])
        self.assertEqual(dead_end, spec["metrics"]["dead_end"])

    def test_consumption_protocol_and_examples_are_documented(self):
        skill = SKILL.read_text(encoding="utf-8")
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn('"${AM[@]}" --json brief', skill)
        self.assertIn('"${AM[@]}" --json context', skill)
        self.assertIn('"${AM[@]}" --json search', skill)
        self.assertIn("One zero match", skill)
        self.assertNotIn("Run this before non-trivial work", skill)
        self.assertIn("brief-first", doc)
        self.assertIn("not proof that the visible scope has no memory", " ".join(doc.split()))

        # Concrete counterparts of every command in the documented daily loop.
        for command in (["brief"], ["context"], ["search", "review", "--limit", "4"]):
            payload = json.loads(self.run_cli(command).stdout)
            self.assert_scope_and_budget(payload)

    def test_execution_does_not_touch_user_instruction_or_runtime_files(self):
        protected = {
            "CLAUDE.md": "user-owned claude instructions\n",
            "AGENTS.md": "user-owned agent instructions\n",
            ".agent-runtime/settings.json": '{"user_owned": true}\n',
        }
        for relative, content in protected.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        # Snapshot the complete fixture root so the explicit settings file, protected
        # user files, file-name set, and all other bytes are covered together.
        before = snapshot_tree(self.root)
        brief = json.loads(self.run_cli(["brief"]).stdout)
        self.run_next_commands(brief)
        no_match = json.loads(self.run_cli(["search", "absent", "--limit", "4"]).stdout)
        self.run_next_commands(no_match)
        self.assertEqual(snapshot_tree(self.root), before)

        implementation = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SKILL, DOC, REPO / "skills" / "agent-memory" / "references" / "browse-and-tags.md")
        )
        for forbidden in ("write claude.md", "write agents.md", "generate claude.md", "generate agents.md"):
            self.assertNotIn(forbidden, implementation.lower())

    def test_permission_error_is_nonzero_and_specific(self):
        denied = self.root / "denied-settings.json"
        denied.write_text(json.dumps({"scope": "project-a", "budget": 4}), encoding="utf-8")
        denied.chmod(0)
        try:
            result = self.run_cli(["brief"], settings=denied, expected=2)
            self.assertEqual(result.stdout, "")
            self.assertIn(f"permission denied reading settings: {denied}", result.stderr)
        finally:
            denied.chmod(stat.S_IRUSR | stat.S_IWUSR)


if __name__ == "__main__":
    unittest.main()
