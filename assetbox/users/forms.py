from django import forms
from django.contrib.auth import get_user_model
# Import UserPreference from core app
from core.models import UserPreference 

User = get_user_model()

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

class UserPreferencesForm(forms.Form):
    # Define fields explicitly
    pagination_per_page = forms.IntegerField(
        label='Items Per Page',
        min_value=1,
    )
    theme = forms.ChoiceField(
        choices=UserPreference.THEME_CHOICES, # Reference choices from core model
        required=False,
        widget=forms.Select()
    ) 