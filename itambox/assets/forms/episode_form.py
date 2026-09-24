from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Div, Layout, Submit
from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from assets.models import Asset, RepairEpisode


class RepairEpisodeForm(forms.ModelForm):
    """Create or edit a repair/replacement episode (#504).

    The episode is a small hub: the unit it is about plus, when one exists, the
    unit that stood in for it. The records of the story link to the episode
    through their own optional episode picker.
    """

    class Meta:
        model = RepairEpisode
        fields = ["asset", "substitute_asset", "notes"]
        widgets = {
            "asset": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "substitute_asset": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Re-evaluate both pickers per request: they are tenant-scoped managers,
        # so a tenant context must be active when the querysets are evaluated.
        self.fields["asset"].queryset = Asset.objects.all()
        self.fields["substitute_asset"].queryset = Asset.objects.all()
        if self.instance.pk:
            # A linked unit stays selectable even when it sits in the recycle
            # bin, so editing the episode never silently drops the link.
            for name in ("asset", "substitute_asset"):
                current_id = getattr(self.instance, f"{name}_id", None)
                if current_id:
                    self.fields[name].queryset = self.fields[name].queryset | Asset.all_objects.filter(pk=current_id)

        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.form_tag = True

        button_text = _("Update") if self.instance and self.instance.pk else _("Create Repair Episode")
        cancel_url = reverse("assets:repairepisode_list")

        self.helper.layout = Layout(
            Div(
                Div("asset", css_class="col-md-6"),
                Div("substitute_asset", css_class="col-md-6"),
                css_class="row",
            ),
            "notes",
            HTML('<div class="mt-3">'),
            Submit("submit", button_text, css_class="btn btn-primary"),
            HTML(f'<a href={cancel_url!r} class="btn btn-outline-secondary ms-2">{_("Cancel")}</a>'),
            HTML("</div>"),
        )
