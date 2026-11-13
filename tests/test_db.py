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

    def test_refresh_page_types_corrects_stored_rows(self):
        from ab_archive.text import infer_page_type

        with TemporaryDirectory() as tmp:
            db = ArchiveDB(Path(tmp) / "archive.sqlite")
            db.migrate()
            db.conn.execute(
                "INSERT INTO tests(key, summary, status, description, jira_url, raw_json, synced_at, page_type) "
                "VALUES ('AB-1', 'HSNL - Property - Tooltip', 'Successful', 'see funnel step 1', 'u', '{}', 'now', 'funnel'), "
                "('AB-2', 'HSNL - Funnel - Extras', 'Successful', '', 'u', '{}', 'now', 'funnel')"
            )
            self.assertEqual(db.refresh_page_types(infer_page_type), 1)
            self.assertEqual(db.refresh_page_types(infer_page_type), 0)
            pages = dict(db.conn.execute("SELECT key, page_type FROM tests").fetchall())
            self.assertEqual(pages, {"AB-1": "property", "AB-2": "funnel"})
            db.close()

    def test_document_exists_reflects_upserts(self):
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
            self.assertFalse(db.document_exists("AB-1", "jira_attachment_pdf", "999"))
            db.upsert_document(
                {
                    "test_key": "AB-1",
                    "source_type": "jira_attachment_pdf",
                    "source_id": "999",
                    "source_url": "https://x/attachment/999",
                    "title": "results.pdf",
                    "text": "extracted pdf text",
                    "raw_json": "{}",
                    "updated_at": "",
                }
            )
            self.assertTrue(db.document_exists("AB-1", "jira_attachment_pdf", "999"))
            self.assertFalse(db.document_exists("AB-1", "jira_attachment_pdf", "other-id"))
            db.close()


if __name__ == "__main__":
    unittest.main()
