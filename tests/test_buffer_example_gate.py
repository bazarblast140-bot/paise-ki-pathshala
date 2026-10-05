import unittest

import buffer_shorts


def _topic(*lines):
    return {"key": "t", "beats": {"hook": list(lines)}}


class ExampleGateTests(unittest.TestCase):
    def test_a_rule_of_thumb_number_needs_no_label(self):
        self.assertTrue(buffer_shorts.example_labelled(_topic("RSI 70 पार", "नीचे 2 गुना wick", "1:2")))

    def test_a_price_percent_year_or_big_number_needs_a_label(self):
        for line in ("₹2,000 का नुकसान", "Rs 500 पर खरीदा", "10% गिरा", "2015 में", "250 पर बंद", "99.5 पर"):
            with self.subTest(line=line):
                self.assertFalse(buffer_shorts.example_labelled(_topic(line)))

    def test_an_example_marker_still_clears_a_large_number(self):
        self.assertTrue(buffer_shorts.example_labelled(_topic("मान लीजिए ₹2,000 का नुकसान")))

    def test_no_number_is_fine(self):
        self.assertTrue(buffer_shorts.example_labelled(_topic("Open = Close")))


if __name__ == "__main__":
    unittest.main()
