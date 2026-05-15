import unittest

from ab_archive.config import JQL_SCOPE


class ScopeTests(unittest.TestCase):
    def test_jql_targets_only_ab_project_and_ab_test_type(self):
        self.assertIn("project = AB", JQL_SCOPE)
        self.assertIn('issuetype = "AB Test"', JQL_SCOPE)
        self.assertNotIn("ATB", JQL_SCOPE)

    def test_jql_limits_to_selected_statuses(self):
        self.assertIn("Implemented", JQL_SCOPE)
        self.assertIn('"No significant Result"', JQL_SCOPE)
        self.assertIn("Successful", JQL_SCOPE)
        self.assertIn("Unsuccessful", JQL_SCOPE)


if __name__ == "__main__":
    unittest.main()
