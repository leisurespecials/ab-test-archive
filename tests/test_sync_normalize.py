import json
import unittest

from ab_archive.sync import normalize_issue


class NormalizeIssueTests(unittest.TestCase):
    def test_normalize_issue_maps_structured_fields(self):
        issue = {
            "id": "1",
            "key": "AB-1",
            "fields": {
                "summary": "HSNL - Property - Tooltip test",
                "status": {"name": "Successful", "statusCategory": {"name": "Done"}},
                "created": "2026-01-01",
                "updated": "2026-01-02",
                "labels": ["general_improvement"],
                "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Trigger on hotel page"}]}]},
                "customfield_10601": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hypothesis text"}]}]},
                "customfield_10602": "2026-01-03",
                "customfield_10603": "2026-01-20",
                "customfield_10700": None,
                "customfield_11901": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "IMPLEMENT VARIATION "},
                                {
                                    "type": "text",
                                    "text": "report",
                                    "marks": [{"type": "link", "attrs": {"href": "https://leisurespecials.atlassian.net/wiki/spaces/AT/pages/12345"}}],
                                },
                            ],
                        }
                    ],
                },
                "customfield_12602": None,
                "customfield_12603": None,
            },
        }
        record, text = normalize_issue("https://leisurespecials.atlassian.net", issue)
        self.assertEqual(record["key"], "AB-1")
        self.assertEqual(record["status"], "Successful")
        self.assertEqual(record["market"], "HSNL")
        self.assertEqual(record["page_type"], "property")
        self.assertEqual(json.loads(record["confluence_urls_json"]), ["12345"])
        self.assertIn("Hypothesis text", text)
        self.assertIn("IMPLEMENT VARIATION", text)


if __name__ == "__main__":
    unittest.main()
