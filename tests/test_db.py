from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ab_archive.db import ArchiveDB


class DBTests(unittest.TestCase):
    def test_replacing_chunks_updates_keyword_index(self):
        with TemporaryDirectory() as tmp:
            db = ArchiveDB(Path(tmp) / "archive.sqlite")
            db.migrate()
            db.upsert_test(
                {
                    "key": "AB-1",
                    "issue_id": "1",
                    "summary": "HSNL - Property - Tooltip",
                    "status": "Successful",
                    "status_category": "Done",
                    "outcome": "Successful",
                    "hypothesis": "",
                    "results": "",
                    "learnings": "",
                    "kpi": "",
                    "description": "",
                    "start_date": None,
                    "end_date": None,
                    "created": "",
                    "updated": "",
                    "labels_json": "[]",
                    "jira_url": "https://x/browse/AB-1",
                    "confluence_urls_json": "[]",
                    "market": "HSNL",
                    "page_type": "property",
                    "raw_json": "{}",
                    "synced_at": "now",
                }
            )
            doc_id = db.upsert_document(
                {
                    "test_key": "AB-1",
                    "source_type": "jira",
                    "source_id": "AB-1",
                    "source_url": "https://x/browse/AB-1",
                    "title": "Tooltip",
                    "text": "checkout property tooltip",
                    "raw_json": "{}",
                    "updated_at": "",
                }
            )
            db.replace_chunks(doc_id, "AB-1", ["checkout property tooltip"])
            db.replace_chunks(doc_id, "AB-1", ["different text"])
            db.commit()

            self.assertEqual(db.keyword_search("checkout"), [])
            self.assertEqual(db.keyword_search("different")[0].key, "AB-1")
            self.assertEqual(db.metadata_search(status="Successful", page_type="property")[0].score, 100.0)
            self.assertEqual(db.metadata_search(statuses=["Successful", "Implemented"], page_type="property")[0].key, "AB-1")
            self.assertEqual(db.stats()["outcome"], [("Successful", 1)])
            db.close()


if __name__ == "__main__":
    unittest.main()
