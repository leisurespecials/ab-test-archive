import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ab_archive.db import ArchiveDB
from ab_archive.distill import DISTILL_VERSION, accept_reason, distill_missing, extract_idea, find_reason

DESCRIPTION = (
    "From mobile user testing we found out people did not know what to do after selecting the arrival date.\n\n"
    "By adding a button that looks disabled the visitor gets the sense of having to complete something.\n\n"
    "Trigger on:\n\n"
    "- hotel pages\n"
    "- if hotel has optional extras (pets allowed does not count)\n"
    "- for (var u = dataLayer.length - 1; u >= 0; u--) {\n\n"
    "Tracking:\n\n"
    "- clicks on the button so we can see if it helps\n"
)
REASON = "From mobile user testing we found out people did not know what to do after selecting the arrival date."


def _reply(*quotes: str) -> str:
    return json.dumps({"quotes": list(quotes)})


class FakeOllama:
    def __init__(self, reply: str = _reply(), fail: bool = False):
        self.reply = reply
        self.fail = fail
        self.calls = 0

    def chat_json(self, messages, seed=0):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Could not reach Ollama.")
        return self.reply


class ExtractIdeaTests(unittest.TestCase):
    def test_idea_is_title_change_plus_trigger_rules_without_code(self):
        idea = extract_idea("HSNL - Website - Improve usability of date picker on mobile ITERATION", DESCRIPTION)
        self.assertEqual(
            idea,
            "Improve usability of date picker on mobile. Applies to: hotel pages; "
            "if hotel has optional extras (pets allowed does not count).",
        )

    def test_every_word_of_the_idea_comes_from_the_ticket(self):
        summary = "HSDE - Property - Show mobile menu on the left"
        idea = extract_idea(summary, DESCRIPTION)
        ticket = (summary + " " + DESCRIPTION).lower()
        for word in idea.replace("Applies to:", "").replace(";", " ").replace(".", " ").split():
            self.assertIn(word.lower(), ticket)

    def test_title_without_a_change_gives_no_idea(self):
        self.assertEqual(extract_idea("HSNL - Checkout", ""), "")
        self.assertEqual(extract_idea("", DESCRIPTION), "")

    def test_older_title_formats_still_name_the_change(self):
        self.assertEqual(extract_idea("HSNL - Validate Consent Mode modal", ""), "Validate Consent Mode modal.")
        self.assertEqual(extract_idea("Show breakfast more prominently", ""), "Show breakfast more prominently.")
        self.assertEqual(
            extract_idea("HSNL - Search - Show price - total instead of per night RERUN", ""),
            "Show price - total instead of per night.",
        )

    def test_tracking_list_is_not_scope(self):
        idea = extract_idea("HSNL - Search - Add wellness label", "Tracking:\n\n- clicks on label\n")
        self.assertEqual(idea, "Add wellness label.")


class AcceptReasonTests(unittest.TestCase):
    def test_verbatim_reason_is_kept(self):
        self.assertEqual(accept_reason(_reply(REASON), DESCRIPTION), REASON)

    def test_reworded_quote_is_dropped(self):
        reworded = "User testing showed that people did not know what to do after picking the arrival date."
        self.assertEqual(accept_reason(_reply(reworded), DESCRIPTION), "")

    def test_plain_description_of_the_change_is_dropped(self):
        description = "Hide the price per night when we show the total price on the hotel page.\n" * 2
        quote = "Hide the price per night when we show the total price on the hotel page."
        self.assertEqual(accept_reason(_reply(quote), description), "")

    def test_list_items_links_and_short_fragments_are_dropped(self):
        description = DESCRIPTION + "\nResearch that might help can be found here: https://example.com/page\n"
        self.assertEqual(
            accept_reason(
                _reply(
                    "if hotel has optional extras (pets allowed does not count)",
                    "clicks on the button so we can see if it helps",
                    "Research that might help can be found here: https://example.com/page",
                    "did not know",
                ),
                description,
            ),
            "",
        )

    def test_follow_up_notes_are_not_reasons(self):
        description = DESCRIPTION + "\nFollow up might be to implement an image gallery on the page.\n"
        quote = "Follow up might be to implement an image gallery on the page."
        self.assertEqual(accept_reason(_reply(quote, REASON), description), REASON)

    def test_invalid_replies_give_nothing(self):
        for raw in ("", "not json", "[]", '{"quotes": "x"}', '{"quotes": [null, 3]}'):
            self.assertEqual(accept_reason(raw, DESCRIPTION), "")

    def test_short_description_is_not_sent_to_the_model(self):
        ollama = FakeOllama(_reply(REASON))
        self.assertEqual(find_reason(ollama, "Trigger on:\n- homepage"), "")
        self.assertEqual(ollama.calls, 0)


def _test(key: str, hypothesis: str = "", description: str = DESCRIPTION, updated: str = "2026-01-02") -> dict:
    return {
        "key": key, "issue_id": key, "summary": f"HSNL - Property - Change number {key[3:]} thing",
        "status": "Successful", "status_category": "Done", "outcome": "Successful",
        "hypothesis": hypothesis, "results": "", "learnings": "", "kpi": "", "description": description,
        "start_date": None, "end_date": None, "created": "", "updated": updated, "labels_json": "[]",
        "jira_url": "https://x/browse/" + key, "confluence_urls_json": "[]", "market": "HSNL",
        "page_type": "property", "raw_json": "{}", "synced_at": "now",
    }


class DistillMissingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = ArchiveDB(Path(self.tmp.name) / "archive.sqlite")
        self.addCleanup(self.db.close)
        self.db.migrate()
        self.db.upsert_test(_test("AB-1"))
        self.db.upsert_test(_test("AB-2", hypothesis="Written by the author"))
        self.db.commit()

    def row(self, key: str):
        return self.db.conn.execute("SELECT idea, idea_reason, idea_stamp FROM tests WHERE key=?", (key,)).fetchone()

    def test_only_tests_without_hypothesis_are_distilled(self):
        ideas, reasons = distill_missing(self.db, FakeOllama(_reply(REASON)))
        self.assertEqual((ideas, reasons), (1, 1))
        self.assertTrue(self.row("AB-1")["idea"].startswith("Change number 1 thing. Applies to: hotel pages"))
        self.assertEqual(self.row("AB-1")["idea_reason"], REASON)
        self.assertEqual(self.row("AB-1")["idea_stamp"], f"{DISTILL_VERSION}|2026-01-02")
        self.assertEqual(tuple(self.row("AB-2")), ("", "", ""))

    def test_done_tests_are_skipped_until_the_ticket_changes(self):
        ollama = FakeOllama(_reply(REASON))
        distill_missing(self.db, ollama)
        distill_missing(self.db, ollama)
        self.assertEqual(ollama.calls, 1)

        self.db.upsert_test(_test("AB-1", updated="2026-03-01"))  # a re-sync keeps the idea columns
        self.assertEqual(self.row("AB-1")["idea_reason"], REASON)
        distill_missing(self.db, ollama)
        self.assertEqual(ollama.calls, 2)
        self.assertEqual(self.row("AB-1")["idea_stamp"], f"{DISTILL_VERSION}|2026-03-01")

    def test_idea_is_cleared_once_a_hypothesis_is_written(self):
        distill_missing(self.db, FakeOllama(_reply(REASON)))
        self.db.upsert_test(_test("AB-1", hypothesis="Now written down"))
        distill_missing(self.db, FakeOllama())
        self.assertEqual(tuple(self.row("AB-1")), ("", "", ""))

    def test_unreachable_model_stores_idea_and_retries_reason_later(self):
        ideas, reasons = distill_missing(self.db, FakeOllama(fail=True))
        self.assertEqual((ideas, reasons), (1, 0))
        self.assertTrue(self.row("AB-1")["idea"])
        self.assertEqual(self.row("AB-1")["idea_stamp"], "")

        distill_missing(self.db, FakeOllama(_reply(REASON)))
        self.assertEqual(self.row("AB-1")["idea_reason"], REASON)

    def test_one_failed_call_does_not_stop_the_run(self):
        for n in range(3, 7):
            self.db.upsert_test(_test(f"AB-{n}", updated=f"2026-02-0{n}"))

        class FlakyOllama(FakeOllama):
            def chat_json(self, messages, seed=0):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("timed out")
                return self.reply

        ideas, reasons = distill_missing(self.db, FlakyOllama(_reply(REASON)))
        self.assertEqual((ideas, reasons), (5, 4))
        pending = self.db.conn.execute("SELECT COUNT(*) FROM tests WHERE hypothesis='' AND idea_stamp=''").fetchone()[0]
        self.assertEqual(pending, 1)

    def test_aborted_generation_counts_as_no_answer(self):
        class AbortingOllama(FakeOllama):
            def chat_json(self, messages, seed=0):
                raise RuntimeError('Ollama returned 500: {"error":"prediction aborted, token repeat limit reached"}')

        distill_missing(self.db, AbortingOllama())
        self.assertEqual(self.row("AB-1")["idea_reason"], "")
        self.assertEqual(self.row("AB-1")["idea_stamp"], f"{DISTILL_VERSION}|2026-01-02")

    def test_migrate_adds_columns_to_an_existing_database(self):
        self.db.conn.execute("ALTER TABLE tests DROP COLUMN idea_reason")
        self.db.migrate()
        self.assertEqual(self.row("AB-1")["idea_reason"], "")


if __name__ == "__main__":
    unittest.main()
