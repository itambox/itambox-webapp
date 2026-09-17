"""#496 repair10: the corrected copy must actually render in German.

Three levels are checked: the compiled German catalog for every new/changed string, the real
form layout of the disposal form (cancel button), and the real financial panel template in
its three states (active disposal / archival freeze / plain estimate).
"""

from decimal import Decimal

from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import translation
from django.utils.translation import gettext
from model_bakery import baker

from assets.forms import AssetDisposalForm
from assets.models import Asset, StatusLabel
from assets.models.choices import DataSanitizationMethodChoices, DisposalMethodChoices
from assets.models.lifecycle import AssetDisposal
from assets.services import cancel_asset_disposal
from core.reports import build_report_context
from core.tests.mixins import TenantTestMixin
from extras.models import ReportTemplate

EXPECTED_GERMAN = {
    "Active Disposals": "Aktive Entsorgungen",
    "Active WEEE-compliant disposals": "Aktive WEEE-konforme Entsorgungen",
    "Proceeds from active disposals": "Erl\u00f6se aus aktiven Entsorgungen",
    "Active Disposal Method Distribution": "Verteilung der aktiven Entsorgungsmethoden",
    "Disposal Status": "Entsorgungsstatus",
    "Cancelled At": "Storniert am",
    "Cancelled By": "Storniert von",
    "Disposal cancellation reason": "Stornogrund der Entsorgung",
    "No active disposal record for this asset.": ("F\u00fcr dieses Asset liegt kein aktiver Entsorgungsnachweis vor."),
    "Disposal value:": "Entsorgungswert:",
    "Archival book value:": "Festgeschriebener Buchwert (Archiv):",
    "Estimated book value:": "Gesch\u00e4tzter Buchwert:",
    "Record:": "Datensatz:",
    "Disposal date:": "Entsorgungsdatum:",
    "Disposal method:": "Entsorgungsmethode:",
    "No archived status is configured, so the disposal cannot be recorded.": (
        "Es ist kein Status \u201eArchiviert\u201c konfiguriert, daher kann die Entsorgung nicht erfasst werden."
    ),
    "No pending status is configured, so the disposal cannot be cancelled.": (
        "Es ist kein Status \u201ePending\u201c konfiguriert, daher kann die Stornierung nicht durchgef\u00fchrt werden."
    ),
}


class DisposalGermanTranslationTests(TenantTestMixin, TestCase):
    def test_every_corrected_string_renders_in_german(self):
        with translation.override("de"):
            for source, german in EXPECTED_GERMAN.items():
                self.assertEqual(gettext(source), german, source)

    def test_bulk_disposal_copy_renders_in_german(self):
        with translation.override("de"):
            rendered = gettext(
                "The system checks in each asset if needed, marks it as out of service and records the "
                "disposal. A submitted proceeds amount becomes the disposal value; without proceeds the book "
                "value is frozen instead. A mistaken disposal can be cancelled afterwards: the record stays in "
                "the history and the asset is moved to a pending status."
            )
        self.assertIn("Erl\u00f6s", rendered)
        self.assertIn("Historie", rendered)
        self.assertIn("\u201ePending\u201c versetzt", rendered)
        self.assertNotIn("Freeze", rendered)

    def test_disposal_form_cancel_button_renders_abbrechen(self):
        # The layout is built per request, so the German request must build it in German.
        # Rendered through the real crispy template tag - the button the user sees.
        from django.template import Context, Template

        with translation.override("de"):
            form = AssetDisposalForm()
            rendered = Template("{% load crispy_forms_tags %}{% crispy form %}").render(Context({"form": form}))
        self.assertIn("Abbrechen", rendered)
        self.assertNotIn(">Cancel<", rendered)

    def test_cancel_label_is_in_the_german_catalog(self):
        with translation.override("de"):
            self.assertEqual(gettext("Cancel"), "Abbrechen")
            self.assertNotEqual(gettext("Cancel"), "Cancel")


class DisposalFinancialPanelGermanTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="German Tenant", slug="german-tenant")
        self.archived = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)
        self.set_active_tenant(self.tenant)

    def _render(self, asset):
        with translation.override("de"):
            return render_to_string("assets/includes/detail/asset_financial.html", {"object": asset})

    def test_state_specific_headings_render_in_german(self):
        plain = baker.make(Asset, name="Plain asset", asset_tag="DE-PLAIN", tenant=self.tenant)
        self.assertIn("Gesch\u00e4tzter Buchwert:", self._render(plain))

        frozen = baker.make(
            Asset, name="Frozen asset", asset_tag="DE-FROZEN", tenant=self.tenant, disposed_at="2026-05-15"
        )
        self.assertIn("Festgeschriebener Buchwert (Archiv):", self._render(frozen))

        disposed = baker.make(
            Asset, name="Disposed asset", asset_tag="DE-DISPOSED", tenant=self.tenant, status=self.archived
        )
        AssetDisposal.objects.create(
            asset=disposed,
            disposal_date="2026-05-15",
            disposal_method=DisposalMethodChoices.DONATION,
            data_sanitization_method=DataSanitizationMethodChoices.NIST_PURGE,
            proceeds=Decimal("150.00"),
            currency="EUR",
        )
        rendered = self._render(disposed)
        self.assertIn("Entsorgungswert:", rendered)
        self.assertNotIn("Gesch\u00e4tzter Buchwert:", rendered)

    def test_shared_subscription_cancellation_reason_is_untouched(self):
        with translation.override("de"):
            self.assertEqual(gettext("Cancellation Reason"), "K\u00fcndigungsgrund")
            self.assertEqual(gettext("Disposal cancellation reason"), "Stornogrund der Entsorgung")
            self.assertNotEqual(gettext("Cancellation Reason"), gettext("Disposal cancellation reason"))


class DisposalReportGermanHeaderTests(TenantTestMixin, TestCase):
    """The rendered report header must carry the disposal-specific German label."""

    def test_rendered_report_header_uses_the_disposal_specific_label(self):
        self.setup_tenant_context(name="DE Report Tenant", slug="de-report-tenant")
        status = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)
        self.set_active_tenant(self.tenant)
        asset = baker.make(Asset, name="DE report asset", asset_tag="DE-RPT", tenant=self.tenant, status=status)
        record = AssetDisposal.objects.create(
            asset=asset,
            disposal_date="2026-05-15",
            disposal_method=DisposalMethodChoices.DONATION,
            data_sanitization_method=DataSanitizationMethodChoices.NIST_PURGE,
            proceeds=Decimal("5.00"),
            currency="EUR",
        )
        cancel_asset_disposal(record, user=self.tenant_user, reason="falsch erfasst")
        template = baker.make(
            ReportTemplate,
            report_type="asset_disposal_eol",
            include_summary_cards=False,
            include_distribution_chart=False,
            tenant=self.tenant,
            included_columns=["disposal_asset", "disposal_cancellation_reason"],
        )
        with translation.override("de"):
            headers, rows, *_rest = build_report_context(template, active_tenant=self.tenant)
        self.assertIn("Stornogrund der Entsorgung", headers)
        self.assertNotIn("K\u00fcndigungsgrund", headers)
        self.assertEqual(rows[0]["Stornogrund der Entsorgung"], "falsch erfasst")
