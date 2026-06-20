from django.test import SimpleTestCase

from core.csv_utils import csv_safe, safe_csv_filename


class CsvUtilsTests(SimpleTestCase):
    """WS7-1/WS4-6: shared CSV-export safety helpers."""

    def test_csv_safe_neutralizes_formula_triggers(self):
        for trigger in ('=', '+', '-', '@', '\t', '\r'):
            self.assertEqual(csv_safe(f'{trigger}HYPERLINK("x")'), f"'{trigger}HYPERLINK(\"x\")")
        self.assertEqual(csv_safe('plain text'), 'plain text')
        self.assertEqual(csv_safe(None), '')
        self.assertEqual(csv_safe(42), '42')

    def test_safe_csv_filename_strips_header_injection(self):
        self.assertEqual(safe_csv_filename('a\r\nb"c\\d'), 'abcd')
        self.assertEqual(safe_csv_filename('   '), 'export')
        self.assertEqual(safe_csv_filename('report'), 'report')
