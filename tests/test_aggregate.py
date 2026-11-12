from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ab_archive.db import DERIVED_COLUMNS, ArchiveDB


def _test(key, status, market, end=None, start=None, created="2020-01-01T00:00:00.000+0100"):
    return {
        "key": key, "issue_id": key, "summary": key, "status": status, "status_category": "Done",
        "outcome": status, "hypothesis": "", "results": "", "learnings": "", "kpi": "", "description": "",
        "start_date": start, "end_date": end, "created": created, "updated": "", "labels_json": "[]",
        "jira_url": "https://x", "confluence_urls_json": "[]", "market": market, "page_type": "property",
        "raw_json": "{}", "synced_at": "now",
    }


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = ArchiveDB(Path(self.tmp.name) / "a.sqlite")
        self.db.migrate()
        for t in (
            _test("AB-1", "Successful", "HSNL", end="2024-02-10"),
            _test("AB-2", "Implemented", "HSNL", end="2024-05-01"),
            _test("AB-3", "Unsuccessful", "HSDE", end="2025-11-30"),
            _test("AB-4", "No significant Result", "HSNL", start="2025-01-05"),
        ):
            self.db.upsert_test(t)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_winners_per_year(self):
        rows = {r["year"]: r for r in self.db.aggregate(["year"])}
        self.assertEqual((rows[2024]["total"], rows[2024]["winners"], rows[2024]["win_rate"]), (2, 2, 1.0))
        self.assertEqual((rows[2025]["total"], rows[2025]["winners"], rows[2025]["win_rate"]), (2, 0, 0.0))

    def test_quarter_and_filters(self):
        rows = self.db.aggregate(["quarter"], market="HSNL", year=2024)
        self.assertEqual([r["quarter"] for r in rows], ["2024-Q1", "2024-Q2"])
        self.assertEqual(len(self.db.aggregate([], outcome="winner")), 1)
        self.assertEqual(self.db.aggregate([], outcome="winner")[0]["total"], 2)

    def test_unknown_group_rejected(self):
        with self.assertRaises(ValueError):
            self.db.aggregate(["nope"])

    def test_derived_columns_and_fallback(self):
        rows = {r["key"]: r for r in self.db.conn.execute(f"SELECT key, {DERIVED_COLUMNS} FROM tests")}
        self.assertEqual(rows["AB-1"]["is_winner"], 1)
        self.assertEqual(rows["AB-3"]["is_winner"], 0)
        self.assertEqual((rows["AB-1"]["year"], rows["AB-1"]["quarter"], rows["AB-1"]["period_source"]), (2024, 1, "end_date"))
        self.assertEqual((rows["AB-4"]["year"], rows["AB-4"]["period_source"]), (2025, "start_date"))


if __name__ == "__main__":
    unittest.main()
