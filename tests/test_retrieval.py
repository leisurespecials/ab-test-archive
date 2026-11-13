import unittest

from ab_archive.retrieval import _fts_query, hybrid_search


class BrokenOllama:
    def embed(self, text):
        raise RuntimeError("offline")


class EmptyDB:
    def __init__(self):
        self.metadata_calls = []

    def keyword_search(self, query, limit=10):
        return []

    def all_embedded_chunks(self):
        return []

    def metadata_search(self, **kwargs):
        self.metadata_calls.append(kwargs)
        return []


class RetrievalTests(unittest.TestCase):
    def test_hybrid_search_falls_back_when_ollama_is_offline(self):
        self.assertEqual(hybrid_search(EmptyDB(), "checkout", BrokenOllama()), [])

    def test_fts_query_strips_question_punctuation_and_stopwords(self):
        self.assertEqual(
            _fts_query("which property page tests were successful?"),
            '"property" OR "page" OR "tests" OR "successful"',
        )

    def test_successful_query_includes_implemented_status(self):
        db = EmptyDB()
        hybrid_search(db, "successful property tests", None)
        self.assertEqual(db.metadata_calls[0]["statuses"], ["Successful", "Implemented"])
        self.assertEqual(db.metadata_calls[0]["page_type"], "property")

    def test_checkout_queries_look_in_funnel_tests(self):
        db = EmptyDB()
        hybrid_search(db, "which checkout tests were unsuccessful", None)
        self.assertEqual(db.metadata_calls[0]["page_type"], "funnel")


if __name__ == "__main__":
    unittest.main()
