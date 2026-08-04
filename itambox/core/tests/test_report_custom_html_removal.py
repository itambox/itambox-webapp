import inspect
from pathlib import Path

from django.test import SimpleTestCase

from extras.forms import ReportTemplateForm
from extras.models import ReportTemplate
from extras.views import ReportTemplatePreviewView


class ReportCustomHTMLRemovalTests(SimpleTestCase):
    def test_report_model_no_longer_exposes_custom_html_fields(self):
        field_names = {field.name for field in ReportTemplate._meta.get_fields()}

        self.assertNotIn("advanced_mode", field_names)
        self.assertNotIn("template_content", field_names)

    def test_report_form_no_longer_exposes_custom_html_fields(self):
        form = ReportTemplateForm()

        self.assertNotIn("advanced_mode", form.fields)
        self.assertNotIn("template_content", form.fields)

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
