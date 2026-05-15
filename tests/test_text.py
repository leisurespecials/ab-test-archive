import unittest

from ab_archive.text import adf_to_text, extract_confluence_page_ids, infer_market, infer_page_type


class TextTests(unittest.TestCase):
    def test_adf_to_text_preserves_links(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Full report: "},
                        {
                            "type": "text",
                            "text": "View",
                            "marks": [{"type": "link", "attrs": {"href": "https://x/wiki/spaces/AT/pages/123"}}],
                        },
                    ],
                }
            ],
        }
        text = adf_to_text(adf)
        self.assertIn("Full report", text)
        self.assertIn("https://x/wiki/spaces/AT/pages/123", text)

    def test_extract_confluence_page_ids(self):
        text = "Report https://leisurespecials.atlassian.net/wiki/spaces/AT/pages/6172835842/name"
        self.assertEqual(extract_confluence_page_ids(text), ["6172835842"])

    def test_infer_market_and_page_type(self):
        summary = "HSNL - Property - Improve usability of total price tooltip"
        self.assertEqual(infer_market(summary), "HSNL")
        self.assertEqual(infer_page_type(summary, ""), "property")
        self.assertEqual(infer_page_type("HSDE - Funnel - Show extras", ""), "funnel")


if __name__ == "__main__":
    unittest.main()
