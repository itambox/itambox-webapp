"""Forms for the command-backed global definition management UI."""

from __future__ import annotations

from django import forms

from extras.models import CustomFieldChoice, CustomFieldChoiceSet


class _RevisionFormMixin(forms.Form):
    expected_resource_revision = forms.CharField(
        required=True,
        widget=forms.HiddenInput,
    )

    def __init__(self, *args, instance=None, expected_resource_revision="", **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        if expected_resource_revision:
            self.fields["expected_resource_revision"].initial = str(expected_resource_revision)

    @property
    def is_managed_definition(self) -> bool:
        definition = self.instance
        if isinstance(definition, CustomFieldChoice):
            definition = definition.choice_set
        return bool(definition and definition.management_kind != CustomFieldChoiceSet.MANAGEMENT_LOCAL)

    def _disable_managed_fields(self, *names: str) -> None:
        if not self.is_managed_definition:
            return
        for name in names:
            if name in self.fields:
                self.fields[name].disabled = True


class ChoiceSetCreateForm(forms.Form):
    namespace = forms.CharField(max_length=64)
    slug = forms.SlugField(max_length=128)
    label = forms.CharField(max_length=255)


class ChoiceSetUpdateForm(_RevisionFormMixin):
    label = forms.CharField(max_length=255, required=False)
    replacement_identity = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance is not None:
            self.fields["label"].initial = self.instance.label
            self.fields["replacement_identity"].initial = self.instance.replaced_by or ""
        self._disable_managed_fields("label", "replacement_identity")


class ChoiceSetRetireForm(_RevisionFormMixin):
    replacement_identity = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance is not None:
            self.fields["replacement_identity"].initial = self.instance.replaced_by or ""
        self._disable_managed_fields("replacement_identity")


class ChoiceCreateForm(forms.Form):
    key = forms.CharField(max_length=128)
    label = forms.CharField(max_length=255)
    position = forms.IntegerField(min_value=1)


class ChoiceUpdateForm(_RevisionFormMixin):
    label = forms.CharField(max_length=255, required=False)
    position = forms.IntegerField(min_value=1, required=False)
    replacement_identity = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance is not None:
            self.fields["label"].initial = self.instance.label
            self.fields["position"].initial = self.instance.position
            self.fields["replacement_identity"].initial = self.instance.replaced_by or ""
        self._disable_managed_fields("label", "position", "replacement_identity")


class ChoiceRetireForm(_RevisionFormMixin):
    replacement_identity = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance is not None:
            self.fields["replacement_identity"].initial = self.instance.replaced_by or ""
        self._disable_managed_fields("replacement_identity")


__all__ = [
    "ChoiceCreateForm",
    "ChoiceRetireForm",
    "ChoiceSetCreateForm",
    "ChoiceSetRetireForm",
    "ChoiceSetUpdateForm",
    "ChoiceUpdateForm",
]
