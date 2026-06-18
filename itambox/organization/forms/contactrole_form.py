from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

from core.forms import FilterForm

from ..models import ContactRole, Contact, ContactAssignment
from ..filters import ContactRoleFilterSet


class ContactRoleForm(forms.ModelForm):
    class Meta:
        model = ContactRole
        fields = ['name', 'slug', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name',
            'slug',
            'description'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:contactrole_list')


class ContactAssignmentForm(forms.ModelForm):
    contact = forms.ModelChoiceField(
        queryset=Contact.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    role = forms.ModelChoiceField(
        queryset=ContactRole.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    priority = forms.ChoiceField(
        choices=[
            ('', '---------'),
            ('primary', _('Primary')),
            ('secondary', _('Secondary')),
            ('tertiary', _('Tertiary')),
            ('inactive', _('Inactive')),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ContactAssignment
        fields = ['contact', 'role', 'priority']

    def __init__(self, *args, **kwargs):
        content_type = kwargs.pop('content_type', None)
        object_id = kwargs.pop('object_id', None)
        super().__init__(*args, **kwargs)
        self.content_type = content_type
        self.object_id = object_id

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'contact',
            'role',
            'priority',
        )
        button_text = 'Assign'
        self.helper.layout.append(
            HTML('<div class="mt-4"></div>')
        )
        self.helper.layout.append(
            Submit('submit', button_text, css_class='btn btn-primary')
        )
        self.helper.layout.append(
            HTML('<button type="button" class="btn btn-outline-secondary ms-2" data-bs-dismiss="modal">Cancel</button>')
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.content_type and self.object_id:
            instance.content_type = self.content_type
            instance.object_id = self.object_id
        if commit:
            instance.save()
        return instance


class ContactRoleFilterForm(FilterForm):
    filterset_class = ContactRoleFilterSet
