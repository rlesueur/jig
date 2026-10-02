"""Tests for report.py. Run with: python3 -m unittest -v"""
import unittest
from decimal import Decimal

import report


class TotalsByShop(unittest.TestCase):
    def setUp(self):
        self.totals = report.totals_by_shop("sales.csv")

    def test_every_shop_is_counted(self):
        self.assertEqual(sorted(self.totals), ["Harrogate", "Leeds", "York"])

    def test_takings_are_quantity_times_price(self):
        self.assertEqual(self.totals["York"], Decimal("165.00"))
        self.assertEqual(self.totals["Harrogate"], Decimal("190.95"))

    def test_prices_with_thousands_separators(self):
        self.assertEqual(self.totals["Leeds"], Decimal("1392.70"))


class Summary(unittest.TestCase):
    def test_summary_lists_shops_from_highest_takings(self):
        lines = report.summary("sales.csv").splitlines()
        self.assertEqual(lines[0], "Leeds: £1,392.70")
        self.assertEqual(lines[1], "Harrogate: £190.95")
        self.assertEqual(lines[2], "York: £165.00")
        self.assertEqual(lines[3], "Total: £1,748.65")


if __name__ == "__main__":
    unittest.main()
