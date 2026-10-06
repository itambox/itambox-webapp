"""Focused RFC 8785 vectors for the T07 release canonicalizer."""

from __future__ import annotations

import unittest

from extras.canonicalization import canonicalize_release_document


class ReleaseCanonicalizationTests(unittest.TestCase):
    def test_ecmascript_number_spelling_vector(self):
        document = {
            "numbers": [333333333.3333333, 1e30, 4.5, 0.002, 1e-27],
            "negative_zero": -0.0,
        }
        self.assertEqual(
            canonicalize_release_document(document),
            b'{"negative_zero":0,"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27]}',
        )

    def test_utf16_code_unit_key_ordering_vector(self):
        # U+1F600 is encoded as D83D DE00 in UTF-16. JCS compares code units,
        # so it precedes U+E000 even though Python compares code points.
        document = {"\U0001f600": "emoji", "\ue000": "bmp"}
        self.assertEqual(canonicalize_release_document(document), '{"😀":"emoji","":"bmp"}'.encode("utf-8"))

    def test_arrays_are_not_domain_sorted(self):
        document = {"definitions": [{"z": 1, "a": 2}, {"z": 3, "a": 4}]}
        self.assertEqual(
            canonicalize_release_document(document),
            b'{"definitions":[{"a":2,"z":1},{"a":4,"z":3}]}',
        )

    def test_non_object_documents_are_rejected(self):
        for value in (None, [], True, 0, "text"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                canonicalize_release_document(value)

    def test_non_json_numbers_and_surrogates_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), 9007199254740992, "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                canonicalize_release_document({"value": value})

    def test_unicode_is_not_normalized(self):
        self.assertEqual(
            canonicalize_release_document({"value": "e\u0301"}),
            '{"value":"e\u0301"}'.encode("utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
