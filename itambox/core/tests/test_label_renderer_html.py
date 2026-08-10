from types import SimpleNamespace

from django.test import SimpleTestCase

from core.tasks.labels import _build_labels_document, _default_label_card, render_label_html


class LabelRendererHTMLTests(SimpleTestCase):
    def setUp(self):
        self.asset = SimpleNamespace(name="Laptop", asset_tag="IT-1", serial_number="SN-1")
        self.barcode_uri = "data:image/png;base64,AAAA"

    def test_default_label_card_uses_classes_instead_of_inline_styles(self):
        rendered = _default_label_card(self.asset, self.barcode_uri)

        self.assertNotIn("style=", rendered.lower())
        self.assertIn('class="label-card-table"', rendered)
        self.assertIn('class="label-card-barcode"', rendered)

    def test_custom_label_is_sanitized_after_jinja_rendering(self):
        label_template = SimpleNamespace(
            name="Custom",
            template_code='<div onclick="alert(1)"><span style="color: #123456">{{ asset.name }}</span><script>x</script></div>',
            barcode_format="qr",
        )

        rendered = render_label_html(self.asset, label_template, self.barcode_uri)

        self.assertNotIn("style=", rendered.lower())
        self.assertNotIn("onclick", rendered.lower())
        self.assertNotIn("<script", rendered.lower())
        self.assertIn("Laptop", rendered)
        self.assertIn("#123456", rendered)

    def test_label_jinja_exposes_scalars_and_escapes_untrusted_values(self):
        self.asset.name = "<b>owned</b>"
        label_template = SimpleNamespace(
            name="Unsafe context",
            template_code="<span>{{ asset.name }}</span>{{ asset.__class__ }}",
            barcode_format="qr",
        )

        rendered = render_label_html(self.asset, label_template, self.barcode_uri)

        self.assertIn("&lt;b&gt;owned&lt;/b&gt;", rendered)
        self.assertNotIn("<b>owned</b>", rendered)
        self.assertNotIn("__class__", rendered)

    def test_custom_label_only_keeps_the_generated_barcode_data_uri(self):
        label_template = SimpleNamespace(
            name="Unsafe resource",
            template_code=('<img src="data:image/png;base64,BBBB"><img src="{{ barcode_data_uri }}">'),
            barcode_format="qr",
        )

        rendered = render_label_html(self.asset, label_template, self.barcode_uri)

        self.assertNotIn("BBBB", rendered)
        self.assertIn(self.barcode_uri, rendered)

    def test_pdf_document_uses_safe_measurements_and_class_page_breaks(self):
        label_template = SimpleNamespace(page_width="2.25in; color: red", page_height=float("inf"))
        document = _build_labels_document(["<div>Label</div>"], label_template, "roll")

        self.assertNotIn("style=", document.lower())
        self.assertIn("size: 2.25in 1.25in", document)
        self.assertIn('class="label-card page-break-avoid"', document)
        self.assertNotIn("color: red", document)

    def test_custom_label_size_limits_fall_back_to_the_default_card(self):
        oversized_source = SimpleNamespace(
            name="Oversized source",
            template_code="x" * (64 * 1024 + 1),
            barcode_format="qr",
        )
        oversized_output = render_label_html(self.asset, oversized_source, self.barcode_uri)
        self.assertIn('class="label-card-table"', oversized_output)

        oversized_rendered = SimpleNamespace(
            name="Oversized output",
            template_code="{{ 'x' * 262145 }}",
            barcode_format="qr",
        )
        rendered_output = render_label_html(self.asset, oversized_rendered, self.barcode_uri)
        self.assertIn('class="label-card-table"', rendered_output)

    def test_custom_template_fallback_redacts_template_and_exception_details(self):
        label_template = SimpleNamespace(
            pk=17,
            name="customer-secret-template",
            template_code="{{ invalid",
            barcode_format="qr",
        )

        with self.assertLogs("core.tasks.labels", level="WARNING") as captured:
            rendered = render_label_html(self.asset, label_template, self.barcode_uri)

        log_output = " ".join(captured.output)
        self.assertIn('class="label-card-table"', rendered)
        self.assertNotIn("customer-secret-template", log_output)
        self.assertNotIn("invalid", log_output)

    def test_grid_document_pads_cards_and_marks_page_boundaries(self):
        label_template = SimpleNamespace()
        document = _build_labels_document(["<div>Label</div>"] * 25, label_template, "a4_grid")

        self.assertIn('class="grid-table page-break-always"', document)
        self.assertIn('class="grid-table page-break-avoid"', document)
        self.assertIn("&nbsp;", document)
