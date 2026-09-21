import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[3] / "skills" / "agent-memory" / "scripts"
sys.path.insert(0, str(SCRIPTS))
store = importlib.import_module("memory_store")
store_ext = importlib.import_module("memory_store_ext")
from memory_config import MemoryRoot


class StorageArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mem = self.root / "mem"
        self.mem.mkdir()
        (self.mem / "topic.md").write_text("---\ntags: [safe]\n---\n# Topic\nneedle body\n")
        (self.mem / "MEMORY.md").write_text("# generated navigation\n")
        (self.mem / "cache").mkdir()
        (self.mem / "cache" / "cached.md").write_text("# Cached\n")
        (self.mem / "backup").mkdir()
        (self.mem / "backup" / "old.md").write_text("# Old\n")
        (self.mem / "topic.backup.md").write_text("# Backup copy\n")
        (self.mem / "backup-strategy.md").write_text("# Legitimate topic\n")
        self.db = self.root / "index.sqlite3"
        self.conn = store_ext.connect_db(self.db)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def roots(self):
        return [
            MemoryRoot(self.mem, "_shared", (), True),
            MemoryRoot(self.mem, "demo", (), False),
        ]

    def test_sync_excludes_management_files_and_deduplicates_shared_root(self):
        result = store.sync_index(self.conn, self.roots())
        self.assertEqual(result["indexed"], 2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM documents").fetchone()[0], 2)
        self.assertEqual(
            {row[0] for row in self.conn.execute("SELECT scope FROM document_scopes")},
            {"_shared", "demo"},
        )
        sources = store.enumerate_sources(
            {
                "status": "resolved",
                "project_id": "demo",
                "global_roots": [self.mem],
                "project_roots": [self.mem],
            }
        )
        self.assertEqual(
            {Path(source["path"]).name for source in sources},
            {"topic.md", "backup-strategy.md"},
        )
        self.assertTrue(
            all(set(source["scopes"]) == {"global", "project"} for source in sources)
        )

    def test_inventory_uses_checkpointed_read_only_database_without_writes(self):
        store.sync_index(self.conn, self.roots())
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()
        os.chmod(self.db, 0o444)
        readonly, _ = store_ext.connect_db_readonly(self.db)
        try:
            before = readonly.total_changes
            inventory = store.navigation_inventory(
                {"status": "resolved", "project_id": "demo", "connection": readonly},
                {},
            )
            self.assertEqual(inventory["counts"], {"distinct": 2, "global": 2, "project": 2})
            self.assertEqual(readonly.total_changes, before)
        finally:
            readonly.close()
        self.conn = sqlite3.connect(":memory:")

    def test_positive_search_ids_order_and_scores_are_unchanged(self):
        (self.mem / "second.md").write_text("# Second\nneedle needle\n")
        store.sync_index(self.conn, self.roots())
        before = store.search_documents(self.conn, "needle", project="demo")
        empty_before = store.search_documents(self.conn, "definitelyabsent", project="demo")
        store.navigation_inventory(
            {"status": "resolved", "project_id": "demo", "connection": self.conn},
            {},
        )
        after = store.search_documents(self.conn, "needle", project="demo")
        empty_after = store.search_documents(self.conn, "definitelyabsent", project="demo")
        self.assertEqual(after, before)
        self.assertEqual(empty_before, [])
        self.assertEqual(empty_after, [])
        self.assertEqual(
            [(x["id"], x["score"]) for x in after],
            [(x["id"], x["score"]) for x in before],
        )


if __name__ == "__main__":
    unittest.main()
