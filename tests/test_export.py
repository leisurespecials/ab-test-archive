import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ab_archive.db import ArchiveDB
from ab_archive.export import export_archive


def _test_row(key: str, updated: str = "2026-01-01") -> dict:
    return {
        "key": key, "issue_id": "1", "summary": f"HSNL - Property - {key}", "status": "Successful",
        "status_category": "Done", "outcome": "Successful", "hypothesis": "h", "results": "r",
        "learnings": "", "kpi": "", "description": "", "start_date": None, "end_date": None,
        "created": "", "updated": updated, "labels_json": '["a"]', "jira_url": f"https://x/browse/{key}",
        "confluence_urls_json": "[]", "market": "HSNL", "page_type": "property",
        "raw_json": '{"secret": "payload"}', "synced_at": "now",
    }


class ExportTests(unittest.TestCase):
    def test_export_is_stable_and_drops_embeddings_and_raw_json(self):
        with TemporaryDirectory() as tmp:
            db = ArchiveDB(Path(tmp) / "archive.sqlite")
            db.migrate()
            db.upsert_test(_test_row("AB-1"))
            out = Path(tmp) / "out"

            self.assertEqual(export_archive(db, out), (1, 2, 0))  # test file + manifest
            self.assertEqual(export_archive(db, out), (1, 0, 0))  # nothing changed -> no diff

            payload = json.loads((out / "tests" / "AB-1.json").read_text())
            self.assertEqual(payload["labels"], ["a"])
            self.assertNotIn("raw_json", payload)
            self.assertNotIn("embedding_json", json.dumps(payload))

    def test_removed_tests_are_deleted_from_export(self):
        with TemporaryDirectory() as tmp:
            db = ArchiveDB(Path(tmp) / "archive.sqlite")
            db.migrate()
            db.upsert_test(_test_row("AB-1"))
            db.upsert_test(_test_row("AB-2"))
            out = Path(tmp) / "out"
            export_archive(db, out)
            db.conn.execute("DELETE FROM tests WHERE key = 'AB-2'")
            self.assertEqual(export_archive(db, out)[2], 1)
            self.assertFalse((out / "tests" / "AB-2.json").exists())


if __name__ == "__main__":
    unittest.main()
