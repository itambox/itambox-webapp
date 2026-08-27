from django.test import SimpleTestCase

from itambox.scanning import strip_itambox_prefix


class StripItamboxPrefixTests(SimpleTestCase):
    def test_normalization_vectors(self):
        vectors = (
            (None, ""),
            ("", ""),
            ("  plain-tag  ", "plain-tag"),
            ('"itambox:ITM-00001"', "ITM-00001"),
            ("\ufeffitambox:\u200bITM-00001", "ITM-00001"),
            ("itambox：ITM-00001", "ITM-00001"),
            ("itambox://ITM-00001/", "ITM-00001"),
            ("itambox://asset/42", "itambox://asset/42"),
        )

        for value, expected in vectors:
            with self.subTest(value=value):
                self.assertEqual(strip_itambox_prefix(value), expected)
