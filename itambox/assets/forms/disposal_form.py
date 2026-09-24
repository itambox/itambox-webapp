from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Div, Fieldset, Layout, Row, Submit
from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from assets.models import Asset, AssetDisposal

from .fields import selectable_episodes


class AssetDisposalForm(forms.ModelForm):
    """Form for recording an AssetDisposal end-of-life record.

    The record is created and amended exclusively through the disposal services
    (``dispose_asset`` / ``update_asset_disposal``), so this form only collects
    validated input: the asset identity of an existing record is immutable
    (issue #496).
    """

    disposal_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        required=True,
    )

    class Meta:
        model = AssetDisposal
        fields = [
            "asset",
            "disposal_method",
            "disposal_date",
            "data_sanitization_method",
            "sanitization_certificate",
            "sanitized_by",
            "recipient",
            "proceeds",
            "currency",
            "weee_compliant",
            "episode",
            "notes",
        ]
        widgets = {
            "asset": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "disposal_method": forms.Select(attrs={"class": "form-select"}),
            "data_sanitization_method": forms.Select(attrs={"class": "form-select"}),
            "sanitization_certificate": forms.TextInput(attrs={"class": "form-control"}),
            "sanitized_by": forms.TextInput(attrs={"class": "form-control"}),
            "recipient": forms.TextInput(attrs={"class": "form-control"}),
            "proceeds": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "currency": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "weee_compliant": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "episode": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # B1: the `asset` ModelChoiceField queryset is frozen at import time when
        # no tenant context is active, so it would otherwise expose (and allow
        # disposal of) every tenant's assets by pk. Re-evaluate it per request so
        # the tenant-scoping manager restricts choices to the active tenant. The
        # disposal views additionally re-fetch the asset through Asset.objects
        # before disposing as defence-in-depth.
        self.fields["asset"].queryset = Asset.objects.all()

        # #504: a linked (soft-deleted) episode stays selectable, so amending the
        # record never silently drops the story link.
        self.fields["episode"].queryset = selectable_episodes(
            self.instance.episode_id if self.instance and self.instance.pk else None
        )

        # #496: a record's asset identity cannot be re-pointed after the fact.
        if self.instance and self.instance.pk:
            self.fields["asset"].disabled = True
            self.fields["asset"].help_text = _("The asset of a disposal record cannot be changed.")

        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.form_tag = True

        button_text = _("Update") if self.instance and self.instance.pk else _("Record Disposal")
        try:
            cancel_url = reverse("assets:assetdisposal_list")
        except Exception:
            cancel_url = "/"

        self.helper.layout = Layout(
            Fieldset(
                _("Disposal Details"),
                Div(
                    Div("asset", css_class="col-md-6"),
                    Div("disposal_date", css_class="col-md-6"),
                    css_class="row",
                ),
                Div(
                    Div("disposal_method", css_class="col-md-6"),
                    Div("recipient", css_class="col-md-6"),
                    css_class="row",
                ),
                Div(
                    Div("episode", css_class="col-md-12"),
                    css_class="row",
                ),
            ),
            Fieldset(
                _("Data Sanitization & Compliance"),
                Div(
                    Div("data_sanitization_method", css_class="col-md-4"),
                    Div("sanitized_by", css_class="col-md-4"),
                    Div("sanitization_certificate", css_class="col-md-4"),
                    css_class="row",
                ),
                Div(
                    Div("weee_compliant", css_class="col-md-4 d-flex align-items-end pb-2"),
                    css_class="row",
                ),
            ),
            Fieldset(
                _("Financial"),
                Div(
                    Div("proceeds", css_class="col-md-4"),
                    Div("currency", css_class="col-md-4"),
                    css_class="row",
                ),
            ),
            "notes",
            HTML('<div class="mt-3">'),
            Submit("submit", button_text, css_class="btn btn-danger"),
            HTML(f'<a href={cancel_url!r} class="btn btn-outline-secondary ms-2">{_("Cancel")}</a>'),
            HTML("</div>"),
        )


class AssetDisposalCancelForm(forms.Form):
    """Cancellation form: the reason is mandatory (issue #496).

    Only the reason is collected; cancellation state itself is owned by
    ``cancel_asset_disposal`` and is never writable through a form field.
    """

    reason = forms.CharField(
        label=_("Cancellation reason"),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text=_("Why was this disposal recorded in error? It is stored on the record and in the audit trail."),
    )

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise forms.ValidationError(_("A cancellation reason is required."))
        return reason

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Fieldset(_("Cancel disposal"), "reason"),
        )
