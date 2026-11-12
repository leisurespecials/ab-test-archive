import unittest

from ab_archive.metrics import parse_results


class MetricsTests(unittest.TestCase):
    def test_structured_template(self):
        m = parse_results("📈 +16.7% lift in CR\n91.6% probability variation wins\nControl 5.54% → Variation 6.47%\nn=2493+2459 users")
        self.assertEqual((m["cr_change_pct"], m["confidence_pct"], m["confidence_favors"]), (16.7, 91.6, "variation"))
        self.assertEqual((m["control_cr_pct"], m["variation_cr_pct"]), (5.54, 6.47))

    def test_control_wins_with_unicode_minus_and_rpo(self):
        m = parse_results("91.4% probability Control beats Variation\n- Conversion Rate: 24.48% → 18.28% (−25.3% relative)\n- Revenue: €1,015 → €1,035 (+1.9%)\n- RPO: €29.00 → €30.43 (+4.9%)")
        self.assertEqual((m["cr_change_pct"], m["revenue_change_pct"], m["rpo_change_pct"]), (-25.3, 1.9, 4.9))
        self.assertEqual(m["confidence_favors"], "control")

    def test_free_text_and_missing(self):
        m = parse_results("CR down by 6,4%, Revenue: up by 14,7%")
        self.assertEqual((m["cr_change_pct"], m["revenue_change_pct"]), (-6.4, 14.7))
        self.assertTrue(all(v is None for v in parse_results("Non significant").values()))
        self.assertTrue(all(v is None for v in parse_results(None).values()))

    def test_unsigned_rate_is_not_a_change(self):
        self.assertIsNone(parse_results("CR: 4.39%")["cr_change_pct"])


if __name__ == "__main__":
    unittest.main()
