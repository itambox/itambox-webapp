from django import forms
from .models import Tag
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML
from django.urls import reverse

class TagForm(forms.ModelForm):
    # Explicitly define color field to allow '#' prefix initially
    color = forms.CharField(
        max_length=7, # Allow 7 chars initially (#aabbcc)
        required=False,
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'})
    )

    class Meta:
        model = Tag
        fields = ['name', 'slug', 'color', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            # 'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'}), # Widget defined above
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
            'color': 'Hexadecimal color code (e.g., 00ff00 for green).'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name', 'slug', 'color', 'description'
        )
        # Use standard button helper if defined, otherwise define here
        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('extras:tag_list')
        self.helper.layout.append(
            HTML('<div class="mt-4"></div>')
        )
        self.helper.layout.append(
            Submit('submit', button_text, css_class='btn btn-primary')
        )
        self.helper.layout.append(
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>')
        )

    def clean_color(self):
        color = self.cleaned_data.get('color')
        if color and color.startswith('#'):
            # Strip the '#' and validate length
            cleaned_color = color[1:]
            if len(cleaned_color) == 6:
                # TODO: Add validation to ensure it's a valid hex code?
                return cleaned_color
            else:
                raise forms.ValidationError("Ensure the color hex code is 6 characters long (after removing '#').")
        elif not color:
             # Allow empty color
            return ''
        # Handle case where color doesn't start with # but might be valid/invalid
        if len(color) == 6:
             # TODO: Add validation to ensure it's a valid hex code?
            return color
        elif len(color) == 0:
            return '' # Allow empty
        else:
             raise forms.ValidationError("Ensure the color hex code is 6 characters long.") 