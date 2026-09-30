import csv
import inspect
import io
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase
from jinja2.exceptions import SecurityError

from core.reports.rendering import render_report_csv, render_report_html
from extras.forms import ReportTemplateForm
from extras.models import ReportTemplate
from extras.views import ReportTemplatePreviewView


class ReportCustomHTMLRemovalTests(SimpleTestCase):
    def test_report_model_and_form_keep_custom_html_without_retired_shape_fields(self):
        fields = {field.name: field for field in ReportTemplate._meta.get_fields()}

        self.assertNotIn("advanced_mode", fields)
        self.assertNotIn("legacy_designer_grandfathered", fields)
        self.assertIn("template_content", fields)

        form = ReportTemplateForm()

        self.assertNotIn("advanced_mode", form.fields)
        self.assertNotIn("legacy_designer_grandfathered", form.fields)
        self.assertIn("template_content", form.fields)

    def test_canonical_csv_uses_columns_and_disclosure(self):
        csv_text = render_report_csv(["Asset Tag"], [{"Asset Tag": "A-1", "Ignored": "value"}], "Sample output")

        self.assertEqual(
            list(csv.reader(io.StringIO(csv_text))),
            [["Asset Tag"], ["A-1"], [], ["Sample output"]],
        )

    def test_custom_html_keeps_autoescaping_and_sandbox_boundary(self):
        rendered = render_report_html(
            {"report_name": "<report>"},
            SimpleNamespace(template_content="<h1>{{ report_name }}</h1>"),
        )
        self.assertIn("&lt;report&gt;", rendered)

        with self.assertRaises(SecurityError):
            render_report_html(
                {"report_name": "Report"},
                SimpleNamespace(template_content="{{ report_name.__class__ }}"),
            )

    def test_preview_srcdoc_is_sandboxed_and_error_text_is_escaped(self):
        root = Path(__file__).resolve().parents[2]
        designer = (root / "static" / "src" / "report-designer.ts").read_text(encoding="utf-8")
        form = (root / "templates" / "core" / "reports" / "report_template_form.html").read_text(encoding="utf-8")

        self.assertNotIn("frame.srcdoc = cleanErr", designer)
        self.assertIn("escapeHtml(cleanErr)", designer)
        self.assertIn('sandbox="allow-same-origin"', form)

    def test_preview_scope_requires_superuser_for_posted_tenant_selection(self):
        source = inspect.getsource(ReportTemplatePreviewView.post)

        self.assertIn("request.user.is_superuser", source)
        self.assertIn("get_current_tenant()", source)
