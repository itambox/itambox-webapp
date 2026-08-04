from collections import OrderedDict
from types import SimpleNamespace

import html5lib
from django.test import SimpleTestCase
from tinycss2 import parse_component_value_list

from core.html_sanitizer import (
    _safe_css_token,
    _sanitize_children,
    _sanitize_element,
    sanitize_label_html,
    sanitize_label_html_for_pdf,
)


class LabelHTMLSanitizerTests(SimpleTestCase):
    def test_rewrites_safe_inline_css_to_nonce_style_block(self):
        rendered = sanitize_label_html(
            '<div class="label"><span style="color: #123456; width: 10mm">Asset</span></div>',
            nonce="test-nonce",
        )

        self.assertIn('class="label"', rendered)
        self.assertIn("Asset", rendered)
        self.assertNotIn("style=", rendered.lower())
        self.assertIn('nonce="test-nonce"', rendered)
        self.assertIn("color:#123456", rendered.replace(" ", ""))
        self.assertIn("width:10mm", rendered.replace(" ", ""))

    def test_drops_scripts_event_handlers_and_unsafe_resources(self):
        rendered = sanitize_label_html_for_pdf(
            """
            <div onclick="alert(1)">
                <script>alert(2)</script>
                <img src="javascript:alert(3)" onerror="alert(4)">
                <img src="data:image/png;base64,AAAA" alt="barcode">
            </div>
            """,
        )

        self.assertNotIn("script", rendered.lower())
        self.assertNotIn("onclick", rendered.lower())
        self.assertNotIn("onerror", rendered.lower())
        self.assertNotIn("javascript:", rendered.lower())
        self.assertIn('src="data:image/png;base64,AAAA"', rendered)
        self.assertIn('alt="barcode"', rendered)

    def test_drops_unsafe_css_declarations_and_user_style_blocks(self):
        rendered = sanitize_label_html_for_pdf(
            """
            <style>.evil { display: none }</style>
            <span style="display: block; background-image: url(https://evil.test/x); position: fixed;">
                Label
            </span>
            """,
        )

        self.assertNotIn(".evil", rendered)
        self.assertNotIn("background-image", rendered)
        self.assertNotIn("position", rendered)
        self.assertIn("<style", rendered.lower())
        self.assertIn("display:block", rendered.replace(" ", ""))
        self.assertIn("Label", rendered)

    def test_rejects_untrusted_data_uris_traversal_and_unsafe_dimensions(self):
        rendered = sanitize_label_html_for_pdf(
            '<img src="/media/../secret.png"><img src="data:image/png;base64,AAAA">'
            '<div style="margin:-1px; width:1001px; color:#abc">safe</div>',
            allowed_data_uris=frozenset({"data:image/png;base64,BBBB"}),
        )

        self.assertNotIn("/media/../secret.png", rendered)
        self.assertNotIn("data:image/png;base64,AAAA", rendered)
        self.assertNotIn("margin:-1px", rendered)
        self.assertNotIn("width:1001px", rendered)
        self.assertIn("color:#abc", rendered)

    def test_reuses_the_same_generated_class_for_identical_styles(self):
        rendered = sanitize_label_html_for_pdf(
            '<span style="font-weight: bold">A</span><span style="font-weight: bold">B</span>',
        )

        self.assertEqual(rendered.count("label-style-"), 3)
        self.assertIn("A", rendered)
        self.assertIn("B", rendered)

    def test_browser_sink_fails_closed_without_nonce(self):
        rendered = sanitize_label_html('<span style="font-weight: bold">Browser</span>')

        self.assertNotIn("<style", rendered.lower())
        self.assertNotIn("style=", rendered.lower())
        self.assertIn("Browser", rendered)

    def test_rejects_invalid_attributes_and_exercises_css_token_boundaries(self):
        rendered = sanitize_label_html_for_pdf(
            '<div id="bad id"><img width="bad" height="1001" colspan="0" rowspan="1000" '
            'align="diagonal" valign="diagonal"><span style="line-height: 2001; background: red !;">safe</span></div>'
        )

        self.assertNotIn("bad id", rendered)
        self.assertNotIn('width="bad"', rendered)
        self.assertIn('height="1001"', rendered)
        self.assertNotIn("diagonal", rendered)
        self.assertNotIn("line-height:2001", rendered.replace(" ", ""))
        self.assertIn("safe", rendered)

        self.assertTrue(_safe_css_token(parse_component_value_list("42")[0]))
        self.assertFalse(_safe_css_token(parse_component_value_list("2001")[0]))
        self.assertTrue(_safe_css_token(parse_component_value_list(",")[0]))
        self.assertFalse(_safe_css_token(parse_component_value_list("!")[0]))
        self.assertFalse(_safe_css_token(parse_component_value_list("url(x)")[0]))

    def test_preserves_tail_text_when_dropping_elements(self):
        fragment = html5lib.parseFragment(
            "<script>bad</script>tail",
            treebuilder="etree",
            namespaceHTMLElements=False,
        )

        _sanitize_children(fragment, OrderedDict(), None)

        self.assertEqual(fragment.text, "tail")
        self.assertFalse(_sanitize_element(SimpleNamespace(tag=object()), OrderedDict(), None))
