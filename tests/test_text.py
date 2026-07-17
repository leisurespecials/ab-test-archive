import unittest

from ab_archive.text import (
    adf_to_text,
    extract_confluence_page_ids,
    infer_market,
    infer_page_type,
    select_pdf_attachments,
)


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

    def test_select_pdf_attachments_filters_by_mime_and_extension(self):
        attachments = [
            {"id": "1", "filename": "results.pdf", "mimeType": "application/octet-stream"},
            {"id": "2", "filename": "screenshot.png", "mimeType": "image/png"},
            {"id": "3", "filename": "report.PDF", "mimeType": "application/pdf"},
            {"id": "4", "filename": "notes.txt", "mimeType": "text/plain"},
        ]
        selected = select_pdf_attachments(attachments)
        self.assertEqual([a["id"] for a in selected], ["1", "3"])

    def test_select_pdf_attachments_handles_none(self):
        self.assertEqual(select_pdf_attachments(None), [])


if __name__ == "__main__":
    unittest.main()
