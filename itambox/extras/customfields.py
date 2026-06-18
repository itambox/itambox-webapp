"""Generic custom-field plumbing (the NetBox way).

A CustomField declares which models it applies to via ``object_types``
(M2M to ContentType). Any model that mixes in ``CustomFieldDataMixin``
stores values in its ``custom_field_data`` JSONField, and any ModelForm that
mixes in ``CustomFieldModelFormMixin`` automatically renders, validates and
persists those fields.

The assets app keeps its richer, fieldset-driven behavior (per-AssetType
spec sheets) on top of this base — see assets/forms/asset_form.py.
"""
from django import forms
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _


def build_custom_field_form_field(cf, initial_value=None):
    """Build a django.forms field for a CustomField definition."""
    from extras.models import CustomField

    common = {'label': cf.label, 'required': cf.required, 'initial': initial_value}
    if cf.field_type == CustomField.FIELD_TYPE_TEXT:
        return forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}), **common)
    if cf.field_type == CustomField.FIELD_TYPE_NUMBER:
        return forms.DecimalField(widget=forms.NumberInput(attrs={'class': 'form-control'}), **common)
    if cf.field_type == CustomField.FIELD_TYPE_DATE:
        return forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), **common)
    if cf.field_type == CustomField.FIELD_TYPE_BOOLEAN:
        common['initial'] = initial_value or False
        return forms.BooleanField(widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), **common)
    if cf.field_type == CustomField.FIELD_TYPE_SELECT:
        choice_lines = [line.strip() for line in (cf.choices or '').split('\n') if line.strip()]
        return forms.ChoiceField(
            choices=[('', '---------')] + [(c, c) for c in choice_lines],
            widget=forms.Select(attrs={'class': 'form-select'}),
            **common,
        )
    return None


def serialize_custom_field_value(value):
    """Normalize a cleaned form value for JSON storage."""
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def custom_fields_for_model(model):
    """All active CustomFields whose object_types include the given model."""
    from extras.models import CustomField
    ct = ContentType.objects.get_for_model(model)
    return CustomField.objects.filter(object_types=ct)


def get_custom_fields_display(obj):
    """Return [(label, value), ...] for an object's stored custom field data,
    resolving display labels from the CustomField definitions."""
    data = getattr(obj, 'custom_field_data', None) or {}
    if not data:
        return []
    labels = dict(custom_fields_for_model(type(obj)).values_list('name', 'label'))
    return [(labels.get(name, name), value) for name, value in sorted(data.items())]


def apply_custom_field_filters(queryset, model, params):
    """Filter a queryset by ``cf_<name>=<value>`` request parameters.

    Values are matched against the JSON storage; numeric and boolean literals
    are coerced so ``?cf_ram_gb=16`` matches a stored number.
    """
    from django.db.models import Q

    for param, value in params.items():
        if not param.startswith('cf_') or value in (None, ''):
            continue
        name = param[3:]
        lookup = f'custom_field_data__{name}'
        q = Q(**{lookup: value})
        if value.lower() in ('true', 'false'):
            q |= Q(**{lookup: value.lower() == 'true'})
        else:
            try:
                q |= Q(**{lookup: int(value)})
            except ValueError:
                try:
                    q |= Q(**{lookup: float(value)})
                except ValueError:
                    pass
        queryset = queryset.filter(q)
    return queryset


class CustomFieldModelFormMixin:
    """ModelForm mixin: render/validate/persist custom fields for Meta.model.

    Injects one ``cf_<name>`` form field per applicable CustomField. Forms
    that build an explicit crispy layout should call
    ``self.append_custom_fields_to_layout()`` after constructing it; forms
    using the generic auto-helper need nothing else.
    """

    custom_fields_fieldset_label = _('Custom Fields')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_field_keys = getattr(self, 'custom_field_keys', [])
        stored = {}
        if self.instance is not None and self.instance.pk:
            stored = getattr(self.instance, 'custom_field_data', None) or {}

        for cf in custom_fields_for_model(self._meta.model):
            key = f'cf_{cf.name}'
            if key in self.fields:
                continue
            form_field = build_custom_field_form_field(cf, stored.get(cf.name))
            if form_field is not None:
                self.fields[key] = form_field
                self.custom_field_keys.append(key)

    def append_custom_fields_to_layout(self):
        """Append injected cf_ fields to an existing crispy helper layout."""
        if not self.custom_field_keys:
            return
        helper = getattr(self, 'helper', None)
        if helper is None or helper.layout is None:
            return
        from crispy_forms.layout import Div, Fieldset
        rows = []
        for i in range(0, len(self.custom_field_keys), 2):
            chunk = self.custom_field_keys[i:i + 2]
            rows.append(Div(*[Div(key, css_class='col-md-6') for key in chunk], css_class='row'))
        helper.layout.append(
            Fieldset(self.custom_fields_fieldset_label, *rows, css_class='mb-4 border p-3 rounded')
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.custom_field_keys:
            data = dict(getattr(instance, 'custom_field_data', None) or {})
            for key in self.custom_field_keys:
                data[key[3:]] = serialize_custom_field_value(self.cleaned_data.get(key))
            instance.custom_field_data = data
        if commit:
            instance.save()
            self.save_m2m()
        return instance
