# itambox/core/forms/mixins.py
#
# Framework-level form mixins and base classes.
# No dependency on extras (or any other domain app).
import json

from django import forms
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.models import ContentType
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, HTML, Div, Submit, Row, Column, Fieldset
from django.urls import reverse
from core.search import SEARCH_INDEXES
from itambox.utils import get_model_viewname
import django_filters
from itambox.middleware import get_current_user


OBJ_TYPE_CHOICES = [
    (
        f"{model._meta.app_label}.{model._meta.model_name}",
        f"{model._meta.app_label.capitalize()} | {model._meta.verbose_name.capitalize()}"
    )
    for model in sorted(SEARCH_INDEXES.keys(), key=lambda m: (m._meta.app_label, m._meta.verbose_name))
]

class SearchForm(forms.Form):
    q = forms.CharField(
        label=_('Search'),
        widget=forms.TextInput(attrs={'placeholder': 'Search ITAMbox', 'class': 'form-control'})
    )
    obj_type = forms.MultipleChoiceField(
        label=_('Object type(s)'),
        choices=OBJ_TYPE_CHOICES,
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'id': 'id_obj_type_select', 'data-tom-select': ''})
    )
    lookup_choices = (
        ('icontains', 'Partial match'),
        ('iexact', 'Exact match'),
        ('istartswith', 'Starts with'),
        ('iendswith', 'Ends with'),
        ('iregex', 'Regex'),
    )
    lookup = forms.ChoiceField(
        label=_('Lookup'),
        choices=lookup_choices,
        required=False,
        initial='icontains',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

class JournalEntryForm(forms.Form):
    comment = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}))


class ConfirmationForm(forms.Form):
    return_url = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if kwargs.get('initial') and 'return_url' in kwargs['initial']:
            self.fields['return_url'].initial = kwargs['initial']['return_url']
        elif instance and hasattr(instance, 'get_absolute_url'):
            self.fields['return_url'].initial = instance.get_absolute_url()
        elif instance:
            try:
                list_view_name = get_model_viewname(instance.__class__, 'list')
                self.fields['return_url'].initial = reverse(list_view_name)
            except Exception:
                pass


BULK_EDIT_FIELD_BLACKLIST = {
    'id', 'pk',
    'created_at', 'updated_at', 'deleted_at',
    'last_audited', 'last_audited_by',
    'signed_at', 'accepted_date', 'verification_hash',
    'slug',
}

BULK_EDIT_FIELD_TYPE_MAP = {
    'CharField': forms.CharField,
    'TextField': lambda **kw: forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), **kw),
    'IntegerField': forms.IntegerField,
    'PositiveIntegerField': forms.IntegerField,
    'BigIntegerField': forms.IntegerField,
    'PositiveBigIntegerField': forms.IntegerField,
    'SmallIntegerField': forms.IntegerField,
    'PositiveSmallIntegerField': forms.IntegerField,
    'FloatField': forms.FloatField,
    'DecimalField': forms.DecimalField,
    'BooleanField': forms.BooleanField,
    'NullBooleanField': forms.NullBooleanField,
    'DateField': forms.DateField,
    'DateTimeField': forms.DateTimeField,
    'EmailField': forms.EmailField,
    'URLField': forms.URLField,
    'ForeignKey': forms.ChoiceField,
}


class BulkEditForm(forms.Form):
    _selected_fields = forms.MultipleChoiceField(
        widget=forms.MultipleHiddenInput(),
        required=False
    )
    add_tags = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        label=_("Add Tags"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    remove_tags = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        label=_("Remove Tags"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''})
    )

    def __init__(self, *args, model=None, **kwargs):
        from extras.models import Tag
        super().__init__(*args, **kwargs)
        self.fields['add_tags'].queryset = Tag.objects.all()
        self.fields['remove_tags'].queryset = Tag.objects.all()

        if model is None:
            return

        from django.db.models import ForeignKey, ManyToManyField
        from django.db.models.fields import related

        choices = [
            ('add_tags', 'add_tags'),
            ('remove_tags', 'remove_tags'),
        ]
        for field in model._meta.get_fields():
            if field.name in BULK_EDIT_FIELD_BLACKLIST:
                continue
            if not field.editable:
                continue
            if isinstance(field, (ManyToManyField, related.RelatedField)) and not isinstance(field, ForeignKey):
                continue
            if field.auto_created and field.name.endswith('_ptr'):
                continue
            if getattr(field, 'primary_key', False):
                continue

            internal_type = field.get_internal_type()
            if getattr(field, 'choices', None):
                form_field_cls = forms.ChoiceField
            else:
                form_field_cls = BULK_EDIT_FIELD_TYPE_MAP.get(internal_type)
            if form_field_cls is None:
                continue

            field_kwargs = {
                'label': getattr(field, 'verbose_name', field.name).title(),
                'required': False,
            }

            if getattr(field, 'choices', None):
                field_kwargs['choices'] = [('', '---------')] + list(field.choices)
            elif isinstance(field, ForeignKey):
                related_model = field.remote_field.model
                if related_model:
                    field_kwargs['choices'] = [('', '---------')] + [
                        (obj.pk, str(obj))
                        for obj in related_model.objects.all()[:200]
                    ]

            self.fields[field.name] = form_field_cls(**field_kwargs)
            choices.append((field.name, field.name))

        self.fields['_selected_fields'].choices = choices

        # Auto-apply Bootstrap classes and TomSelect attribute to all fields in BulkEditForm
        for field_name, field in self.fields.items():
            if field_name == '_selected_fields':
                continue

            # Apply dynamic classes
            existing_classes = field.widget.attrs.get('class', '').split()
            if isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                target_class = 'form-check-input'
            elif isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                target_class = 'form-select'
            else:
                target_class = 'form-control'

            if target_class not in existing_classes:
                existing_classes.append(target_class)
                field.widget.attrs['class'] = ' '.join(existing_classes)

            # Auto-apply TomSelect attribute to all select fields (excluding CheckboxSelectMultiple/RadioSelect/listboxes)
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) and not isinstance(field.widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                if 'size' in field.widget.attrs:
                    continue
                widget_classes = field.widget.attrs.get('class', '')
                if 'available-columns' not in widget_classes and 'selected-columns' not in widget_classes:
                    if 'data-tom-select' not in field.widget.attrs:
                        field.widget.attrs['data-tom-select'] = ''


class CrispyFormMixin:
    """
    Form mixin to auto-initialize FormHelper with standard settings,
    reducing crispy FormHelper boilerplate across all forms.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not hasattr(self, 'helper') or self.helper is None:
            self.helper = FormHelper(self)
            self.helper.form_method = 'post'
            self.helper.form_tag = True

    def action_buttons(self, cancel_url):
        """Return the standard Submit + Cancel button row as crispy elements.

        Splice into a form's Layout to avoid re-implementing the same block in
        every form, e.g.::

            self.helper.layout = Layout(..., *self.action_buttons(cancel_url))

        ``cancel_url`` is a resolved URL string. The submit label reflects
        create vs. update based on the bound instance.
        """
        from django.utils.translation import gettext as _
        from crispy_forms.layout import HTML, Submit
        label = _('Update') if getattr(self, 'instance', None) and self.instance.pk else _('Create')
        return [
            HTML('<div class="mt-4"></div>'),
            Submit('submit', label, css_class='btn btn-primary'),
            HTML(
                f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2" '
                f'data-no-dirty-track="true">{_("Cancel")}</a>'
            ),
        ]


class SlugModelForm(CrispyFormMixin, forms.ModelForm):
    class Media:
        js = (
            'js/slugify.js',
        )

class FilterForm(forms.Form):
    filterset_class = None

    def __init__(self, *args, **kwargs):
        self.queryset = kwargs.pop('queryset', None)
        super(FilterForm, self).__init__(*args, **kwargs)

        if self.filterset_class is None:
            raise NotImplementedError("'filterset_class' must be defined on the FilterForm subclass.")

        filterset_data = args[0] if args else kwargs.get('data', None)
        self.filterset = self.filterset_class(filterset_data, queryset=self.queryset)

        for name, filter_field in self.filterset.filters.items():
            if hasattr(filter_field, 'field'):
                self.fields[name] = filter_field.field
            else:
                field_type = forms.CharField
                if isinstance(filter_field, django_filters.BooleanFilter):
                    field_type = forms.BooleanField
                elif isinstance(filter_field, django_filters.NumberFilter):
                    field_type = forms.DecimalField
                elif isinstance(filter_field, django_filters.DateFilter):
                    field_type = forms.DateField
                elif isinstance(filter_field, django_filters.DateTimeFilter):
                    field_type = forms.DateTimeField
                elif isinstance(filter_field, django_filters.MultipleChoiceFilter):
                    self.fields[name] = forms.MultipleChoiceField(
                        label=filter_field.label if filter_field.label else name.replace('_', ' ').capitalize(),
                        required=False,
                        choices=filter_field.extra.get('choices', [])
                    )
                    continue

                self.fields[name] = field_type(
                    label=filter_field.label if filter_field.label else name.replace('_', ' ').capitalize(),
                    required=False
                )

        self.helper = FormHelper()
        self.helper.form_method = 'get'
        self.helper.form_tag = False

        ajax_fields = getattr(self, 'ajax_fields', None)
        if ajax_fields:
            self.setup_ajax_fields(ajax_fields, filterset_data)

        # Auto-apply TomSelect attribute to all select fields (excluding CheckboxSelectMultiple/RadioSelect/listboxes)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) and not isinstance(field.widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                if 'size' in field.widget.attrs:
                    continue
                widget_classes = field.widget.attrs.get('class', '')
                if 'available-columns' not in widget_classes and 'selected-columns' not in widget_classes:
                    if 'data-tom-select' not in field.widget.attrs:
                        field.widget.attrs['data-tom-select'] = ''

    def setup_ajax_fields(self, ajax_fields, filterset_data):
        for field_name, config in ajax_fields.items():
            if field_name not in self.fields:
                continue

            field = self.fields[field_name]
            url = reverse(config['url_name'])

            field.widget.attrs.update({
                'data-tom-select': '',
                'data-tom-select-url': url,
                'data-tom-select-value-field': config.get('value_field', 'id'),
                'data-tom-select-label-field': config.get('label_field', 'name'),
            })

            if hasattr(field, 'queryset'):
                selected_vals = []
                if filterset_data:
                    if hasattr(filterset_data, 'getlist'):
                        selected_vals = filterset_data.getlist(field_name)
                    else:
                        val = filterset_data.get(field_name)
                        if val:
                            if isinstance(val, list):
                                selected_vals = val
                            else:
                                selected_vals = [val]
                elif self.initial and field_name in self.initial:
                    val = self.initial[field_name]
                    if isinstance(val, list):
                        selected_vals = val
                    else:
                        selected_vals = [val]

                # Convert model instances to PK/to_field_name values if necessary, and filter empty values
                to_field = getattr(field, 'to_field_name', None) or 'pk'
                cleaned_vals = []
                for val in selected_vals:
                    if val is None or val == '':
                        continue
                    if hasattr(val, to_field):
                        cleaned_vals.append(getattr(val, to_field))
                    elif hasattr(val, 'pk'):
                        cleaned_vals.append(val.pk)
                    else:
                        cleaned_vals.append(val)

                if cleaned_vals:
                    filter_kwargs = {f"{to_field}__in": cleaned_vals}
                    field.queryset = field.queryset.filter(**filter_kwargs)
                else:
                    field.queryset = field.queryset.none()

    def search(self):
        if self.is_valid():
            return self.filterset.qs
        return self.filterset.queryset

    @property
    def applied_filters(self):
        if not self.filterset or not self.filterset.data:
            return {}

        applied = {}
        ignored_params = ['page', 'per_page', 'q']

        for name, filter_field in self.filterset.filters.items():
            if name in ignored_params:
                continue

            value = self.filterset.data.getlist(name) if hasattr(self.filterset.data, 'getlist') else self.filterset.data.get(name)

            if value:
                if isinstance(value, list):
                    value = [v for v in value if v != '']
                    if value:
                        applied[name] = value
                elif value != '':
                    applied[name] = value

        return applied


class ColorFieldFormMixin:
    """
    Mixin for forms with a 'color' hex field. Ensures '#' is prepended for the picker
    and cleaned up to raw hex when validating/saving.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if hasattr(self, 'instance') and self.instance and getattr(self.instance, 'color', None):
            self.initial['color'] = f"#{self.instance.color}"

    def clean_color(self):
        color = self.cleaned_data.get('color')
        if color and color.startswith('#'):
            cleaned_color = color[1:]
            if len(cleaned_color) == 6:
                return cleaned_color
            else:
                raise forms.ValidationError("Ensure the color hex code is 6 characters long (after removing '#').")
        elif not color:
            return ''
        if len(color) == 6:
            return color
        elif len(color) == 0:
            return ''
        else:
            raise forms.ValidationError("Ensure the color hex code is 6 characters long.")
