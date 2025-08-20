from django import forms
from core.forms import SlugModelForm, BootstrapMixin
from ..models import Category


class CategoryForm(SlugModelForm, BootstrapMixin):
    class Meta:
        model = Category
        fields = ['name', 'slug', 'color', 'description', 'applies_to', 'email_on_checkout', 'email_on_checkin', 'require_acceptance', 'email_eula', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '00ff00'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'applies_to': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': '["asset", "accessory", "license"]'}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }
