import unittest

from app.routes import _normalize_phone, _parse_optional_float


class ParseOptionalFloatTest(unittest.TestCase):
    def test_empty_string_becomes_none(self):
        # The actual production bug: a bill-type form hides irrelevant fields
        # (e.g. no Electricity input on a Rent bill) but still submits them
        # as "" rather than omitting the key -- Postgres rejects "" outright
        # for a numeric column, so this must be caught before reaching the model.
        self.assertIsNone(_parse_optional_float(""))

    def test_none_stays_none(self):
        self.assertIsNone(_parse_optional_float(None))

    def test_numeric_string_parses(self):
        self.assertEqual(_parse_optional_float("123.45"), 123.45)

    def test_number_passes_through(self):
        self.assertEqual(_parse_optional_float(50), 50.0)

    def test_zero_is_not_treated_as_empty(self):
        self.assertEqual(_parse_optional_float(0), 0.0)
        self.assertEqual(_parse_optional_float("0"), 0.0)


if __name__ == "__main__":
    unittest.main()


class NormalizePhoneTest(unittest.TestCase):
    def test_plain_ten_digits(self):
        self.assertEqual(_normalize_phone("9876543210"), "9876543210")

    def test_strips_country_code_and_separators(self):
        self.assertEqual(_normalize_phone("+91 98765-43210"), "9876543210")
        self.assertEqual(_normalize_phone("09876543210"), "9876543210")

    def test_rejects_invalid(self):
        for bad in (None, "", "12345", "5876543210", "98765432101234", "abcdefghij"):
            self.assertIsNone(_normalize_phone(bad), bad)
