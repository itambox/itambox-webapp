from django.test import SimpleTestCase

from core.context import reset_current_csp_nonce, set_current_csp_nonce
from core.html_styles import color_chip_class, length_class, percentage_class, status_color_class, status_tint_class


class DynamicHTMLStyleTests(SimpleTestCase):
    def test_status_color_class_emits_nonce_style_and_validates_color(self):
        token = set_current_csp_nonce("nonce-test")
        try:
            class_name, style_block = status_color_class("#12ab34")
            fallback_class, fallback_style = status_color_class("red; color: expression(alert(1))")
        finally:
            reset_current_csp_nonce(token)

        self.assertRegex(class_name, r"^status-color-[0-9a-f]{16}$")
        self.assertIn('nonce="nonce-test"', style_block)
        self.assertIn("#12ab34", style_block)
        self.assertNotIn("style=", style_block)
        self.assertNotEqual(class_name, fallback_class)
        self.assertIn("#6c757d", fallback_style)
        self.assertNotIn("expression", fallback_style)

    def test_status_tint_and_percentage_values_are_bounded(self):
        token = set_current_csp_nonce("nonce-test")
        try:
            tint_class, tint_style = status_tint_class("#abcdef")
            percentage_name, percentage_style = percentage_class("250", prefix="budget-width")
        finally:
            reset_current_csp_nonce(token)

        self.assertTrue(tint_class.startswith("status-tint-"))
        self.assertIn("#abcdef1a", tint_style)
        self.assertIn('nonce="nonce-test"', percentage_style)
        self.assertTrue(percentage_name.startswith("budget-width-"))
        self.assertIn("width:100%;", percentage_style)

    def test_style_helpers_cover_invalid_numbers_lengths_colors_and_nonce_fallbacks(self):
        token = set_current_csp_nonce("nonce-test")
        try:
            invalid_percentage = percentage_class("not-a-number")
            non_finite_percentage = percentage_class("NaN")
            decimal_percentage = percentage_class("12.50")
            invalid_length = length_class("not-a-length")
            oversized_length = length_class("201px")
            short_color = color_chip_class("#abc")
        finally:
            reset_current_csp_nonce(token)

        self.assertIn("width:0%;", invalid_percentage[1])
        self.assertIn("width:0%;", non_finite_percentage[1])
        self.assertIn("width:12.5%;", decimal_percentage[1])
        self.assertIn("height:36px;", invalid_length[1])
        self.assertIn("height:36px;", oversized_length[1])
        self.assertIn("#aabbcc", short_color[1])

        _class_name, nonce_less_style = status_color_class("#123456")
        self.assertEqual(nonce_less_style, "")
