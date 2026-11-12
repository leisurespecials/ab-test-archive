from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from ab_archive import sync as sync_module
from ab_archive.config import Config
from ab_archive.db import ArchiveDB
from ab_archive.http import HttpClient


def _issue(key: str, updated: str, status: str = "Successful") -> dict:
    return {
        "id": key.split("-")[1],
        "key": key,
        "fields": {
            "summary": f"HSNL - Property - Test {key}",
            "status": {"name": status, "statusCategory": {"name": "Done"}},
            "created": "2026-01-01",
            "updated": updated,
            "labels": [],
            "description": None,
            "attachment": [],
        },
    }


class FakeClient:
    """Stands in for AtlassianClient; `issues` is what Jira currently has in scope."""

    issues: dict[str, dict] = {}
    failing_comments: set[str] = set()
    fetched: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def iter_scope_index(self, progress=None):
        for key, issue in self.issues.items():
            yield key, issue["fields"]["updated"]

    def iter_ab_test_issues(self, progress=None, keys=None):
        for key in keys if keys is not None else list(self.issues):
            if key in self.issues:
                FakeClient.fetched.append(key)
                yield self.issues[key]

    def get_issue_comments(self, key):
        if key in self.failing_comments:
            raise RuntimeError(f"GET comments for {key} failed with 500")
        return [{"author": {"displayName": "Dex"}, "created": "2026-01-05T10:00", "body": f"comment on {key}"}]

    def get_confluence_page(self, page_id):
        raise AssertionError("no Confluence pages in these fixtures")


class SyncRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(
            jira_base_url="https://example.atlassian.net",
            jira_email="a@b.c",
            jira_api_token="t",
            db_path=Path(self.tmp.name) / "archive.sqlite",
            ollama_base_url="http://localhost:1",
            ollama_chat_model="x",
            ollama_embed_model="x",
        )
        FakeClient.issues = {f"AB-{n}": _issue(f"AB-{n}", "2026-01-02") for n in range(1, 6)}
        FakeClient.failing_comments = set()
        FakeClient.fetched = []
        patcher = mock.patch.object(sync_module, "AtlassianClient", FakeClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_sync(self, **kwargs):
        FakeClient.fetched = []
        self.log: list[str] = []
        return sync_module.sync_archive(self.config, embed=False, progress=self.log.append, **kwargs)

    def stored(self) -> dict[str, str]:
        db = ArchiveDB(self.config.db_path)
        try:
            return db.test_updated_map()
        finally:
            db.close()

    def test_first_run_is_full_then_only_changed_items_are_fetched(self):
        issues, _, _ = self.run_sync()
        self.assertEqual(issues, 5)
        self.assertEqual(len(self.stored()), 5)

        self.run_sync()
        self.assertEqual(FakeClient.fetched, [])

        FakeClient.issues["AB-3"] = _issue("AB-3", "2026-02-01", status="Implemented")
        FakeClient.issues["AB-9"] = _issue("AB-9", "2026-02-02")
        issues, _, _ = self.run_sync()
        self.assertEqual(sorted(FakeClient.fetched), ["AB-3", "AB-9"])
        self.assertEqual(issues, 2)
        self.assertEqual(self.stored()["AB-3"], "2026-02-01")

    def test_full_flag_and_weekly_interval_refetch_everything(self):
        self.run_sync()
        self.run_sync(full=True)
        self.assertEqual(len(FakeClient.fetched), 5)

        db = ArchiveDB(self.config.db_path)
        old = datetime.now(timezone.utc) - sync_module.FULL_SYNC_INTERVAL - timedelta(hours=1)
        db.set_state("last_full_sync", old.isoformat())
        db.commit()
        db.close()
        self.run_sync()
        self.assertEqual(len(FakeClient.fetched), 5)

    def test_one_failing_item_is_skipped_rolled_back_and_retried(self):
        FakeClient.failing_comments = {"AB-2"}
        issues, _, _ = self.run_sync()
        self.assertEqual(issues, 4)
        self.assertNotIn("AB-2", self.stored())  # nothing half-written
        self.assertTrue(any("skipped AB-2" in line for line in self.log))

        db = ArchiveDB(self.config.db_path)
        docs = db.conn.execute("SELECT COUNT(*) FROM documents WHERE test_key='AB-2'").fetchone()[0]
        db.close()
        self.assertEqual(docs, 0)

        FakeClient.failing_comments = set()
        self.run_sync()
        self.assertEqual(FakeClient.fetched, ["AB-2"])
        self.assertIn("AB-2", self.stored())

    def test_failed_update_keeps_previous_version(self):
        self.run_sync()
        FakeClient.issues["AB-4"] = _issue("AB-4", "2026-03-01", status="Unsuccessful")
        FakeClient.failing_comments = {"AB-4"}
        self.run_sync()
        self.assertEqual(self.stored()["AB-4"], "2026-01-02")

    def test_items_that_left_scope_are_removed_with_documents(self):
        self.run_sync()
        del FakeClient.issues["AB-5"]
        self.run_sync()
        self.assertNotIn("AB-5", self.stored())
        db = ArchiveDB(self.config.db_path)
        self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM documents WHERE test_key='AB-5'").fetchone()[0], 0)
        self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM chunks WHERE test_key='AB-5'").fetchone()[0], 0)
        self.assertEqual(db.keyword_search('"AB-5"'), [])
        self.assertTrue(db.keyword_search('"AB-4"'))
        db.close()

    def test_suspiciously_large_removal_is_refused(self):
        self.run_sync()
        with mock.patch.object(sync_module, "MIN_PRUNE_ALLOWANCE", 1):
            FakeClient.issues = {"AB-1": FakeClient.issues["AB-1"]}
            self.run_sync()
        self.assertEqual(len(self.stored()), 5)
        self.assertTrue(any("not removing anything" in line for line in self.log))

    def test_empty_scope_listing_removes_nothing(self):
        self.run_sync()
        FakeClient.issues = {}
        self.run_sync()
        self.assertEqual(len(self.stored()), 5)


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.payload


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://x/y", code, "err", {}, BytesIO(b"boom"))


class HttpRetryTests(unittest.TestCase):
    def client(self, retries=3):
        return HttpClient("https://x", "e", "t", retries=retries, backoff=0)

    def test_retries_server_errors_and_network_errors(self):
        outcomes = [_http_error(500), URLError("dns"), TimeoutError("read timed out"), _Response(b'{"ok": true}')]
        with mock.patch("ab_archive.http.urlopen", side_effect=outcomes) as urlopen:
            self.assertEqual(self.client().request("GET", "/y"), {"ok": True})
        self.assertEqual(urlopen.call_count, 4)

    def test_gives_up_after_retries(self):
        with mock.patch("ab_archive.http.urlopen", side_effect=[_http_error(500) for _ in range(3)]) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "failed with 500: boom"):
                self.client(retries=2).request("GET", "/y")
        self.assertEqual(urlopen.call_count, 3)

    def test_does_not_retry_client_errors(self):
        with mock.patch("ab_archive.http.urlopen", side_effect=[_http_error(404)]) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "failed with 404"):
                self.client().request("GET", "/y")
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
