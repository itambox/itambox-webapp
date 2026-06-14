from django import forms
from core.forms import SlugModelForm
from ..models import Supplier


from extras.customfields import CustomFieldModelFormMixin

class SupplierForm(CustomFieldModelFormMixin, SlugModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'slug', 'website', 'address', 'notes', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'website': forms.URLInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }
