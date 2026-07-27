import unittest
from pathlib import Path

from scripts.openapi_identity import DiagnosticIdentity, IdentityError, parse_diagnostic

REPO_ROOT = Path("C:/work/itambox")


class DiagnosticIdentityTests(unittest.TestCase):
    def test_repo_source_becomes_posix_relative_identity_without_line_number(self):
        raw = (
            r"C:\work\itambox\itambox\assets\api\serializers.py: Warning "
            '[AssetViewSet > AssetSerializer]: unable to resolve type hint for "get_assigned_to".'
        )

        identity = parse_diagnostic(raw, "warning", REPO_ROOT)

        self.assertEqual(
            identity,
            DiagnosticIdentity(
                severity="warning",
                location="itambox/assets/api/serializers.py",
                breadcrumb="AssetViewSet > AssetSerializer",
                message='unable to resolve type hint for "get_assigned_to".',
            ),
        )

    def test_sourceless_diagnostic_uses_unknown_location(self):
        identity = parse_diagnostic(
            'Warning: operationId "assets_assets_update" has collisions.',
            "warning",
            REPO_ROOT,
        )

        self.assertEqual(identity.location, "<unknown>")
        self.assertEqual(identity.breadcrumb, "")

    def test_site_packages_source_uses_portable_external_module_token(self):
        raw = (
            r"C:\work\itambox\.venv\Lib\site-packages\drf_spectacular\openapi.py: "
            "Error [ExampleView]: external diagnostic"
        )

        identity = parse_diagnostic(raw, "error", REPO_ROOT)

        self.assertEqual(identity.location, "<external>:drf_spectacular.openapi")

    def test_breadcrumb_whitespace_is_collapsed(self):
        identity = parse_diagnostic(
            "Warning [ AssetViewSet   >  AssetSerializer ]: message",
            "warning",
            REPO_ROOT,
        )

        self.assertEqual(identity.breadcrumb, "AssetViewSet > AssetSerializer")

    def test_generator_trace_line_number_is_removed_from_identity(self):
        raw = r"C:\work\itambox\itambox\assets\serializers.py:141: Warning [Asset]: message"

        identity = parse_diagnostic(raw, "warning", REPO_ROOT)

        self.assertEqual(identity.location, "itambox/assets/serializers.py")

    def test_declared_severity_must_match_the_rendered_diagnostic(self):
        with self.assertRaisesRegex(IdentityError, "severity"):
            parse_diagnostic("Warning: message", "error", REPO_ROOT)

    def test_ansi_absolute_paths_in_messages_and_control_characters_are_rejected(self):
        cases = (
            ("\x1b[31mWarning: message", "ANSI"),
            (r"Warning: failed at C:\work\secret.py", "absolute path"),
            ("Warning: bad\x00message", "control character"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(IdentityError, expected):
                    parse_diagnostic(raw, "warning", REPO_ROOT)

    def test_identity_json_has_fixed_keys_and_sort_order(self):
        first = DiagnosticIdentity("warning", "z.py", "B", "message")
        second = DiagnosticIdentity("error", "a.py", "A", "message")

        self.assertEqual(
            second.as_dict(),
            {
                "severity": "error",
                "location": "a.py",
                "breadcrumb": "A",
                "message": "message",
            },
        )
        self.assertEqual(sorted([first, second]), [second, first])


if __name__ == "__main__":
    unittest.main()
