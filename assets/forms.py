from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset, Div, Submit
from .models import Asset, AssetRole, Manufacturer # Updated import
from assetbox.organization.models import Location, AssetHolder # Absolute import

# Form for AssetRole (renamed from Category)
class AssetRoleForm(forms.ModelForm):
    class Meta:
        model = AssetRole # Updated model
        fields = ['name', 'slug', 'description']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        # Remove default form tag to allow rendering within custom template or HTMX partials
        self.helper.form_tag = True 
        # Add submit button if form_tag is True, otherwise expect button in template
        self.helper.add_input(Submit('submit', 'Save'))

# Form for Manufacturer
class ManufacturerForm(forms.ModelForm):
    class Meta:
        model = Manufacturer
        fields = ['name', 'slug', 'description']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.add_input(Submit('submit', 'Save'))

# Form for Asset
class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            'name', 'asset_tag', 'serial_number', 'status', 'asset_role', # Updated field
            'manufacturer', 'location', 'purchase_date', 'purchase_cost',
            'warranty_end_date', 'notes'
        ]
        widgets = {
            'purchase_date': forms.DateInput(attrs={'type': 'date'}),
            'warranty_end_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True # Assume we want the full form tag in generic views

        # Define the form layout using Crispy Forms
        self.helper.layout = Layout(
            Fieldset(
                'Main Details',
                Div(
                    Div('name', css_class='col-md-6'),
                    Div('status', css_class='col-md-6'),
                    css_class='row'
                ),
                Div(
                    Div('asset_tag', css_class='col-md-6'),
                    Div('serial_number', css_class='col-md-6'),
                    css_class='row'
                ),
            ),
            Fieldset(
                'Classification & Location',
                Div(
                    Div('asset_role', css_class='col-md-4'), # Updated field
                    Div('manufacturer', css_class='col-md-4'),
                    Div('location', css_class='col-md-4'),
                    css_class='row'
                )
            ),
            Fieldset(
                'Purchase & Warranty',
                Div(
                    Div('purchase_date', css_class='col-md-4'),
                    Div('purchase_cost', css_class='col-md-4'),
                    Div('warranty_end_date', css_class='col-md-4'),
                    css_class='row'
                )
            ),
            Fieldset(
                'Other',
                'notes'
            ),
            Submit('submit', 'Save Asset')
        )

# Form for Asset Checkout Modal
class AssetCheckOutForm(forms.Form):
    # Choice field to select either a holder or a location
    ASSIGN_CHOICES = (
        ('', '--------- Select Target ---------'),
        ('holder', 'Assign to Asset Holder'),
        ('location', 'Assign to Location'),
    )
    # Use CharField for the radio select behavior
    assign_to_type = forms.ChoiceField(
        choices=ASSIGN_CHOICES, 
        required=True,
        widget=forms.RadioSelect,
        label="Assign To"
    )
    
    asset_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all(), # TODO: Filter/optimize queryset?
        required=False,
        label="Asset Holder",
        help_text="Select the person or entity receiving the asset."
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(), # TODO: Filter/optimize queryset?
        required=False,
        label="Location",
        help_text="Select the location where the asset will be stored/used."
    )

    # Field for checkout notes (optional)
    checkout_notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}), 
        required=False,
        label="Checkout Notes"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        # Important: Set form_tag to False because the modal template will provide the <form> tag
        self.helper.form_tag = False 
        self.helper.layout = Layout(
            'assign_to_type',
            # Use Divs to potentially hide/show based on radio selection via JS later
            Div(
                'asset_holder', 
                css_id='div_id_asset_holder' # Add ID for easier JS targeting
            ),
            Div(
                'location',
                css_id='div_id_location' # Add ID for easier JS targeting
            ),
            'checkout_notes',
            # Submit button is expected to be in the modal template footer
        )

    def clean(self):
        cleaned_data = super().clean()
        assign_to_type = cleaned_data.get("assign_to_type")
        asset_holder = cleaned_data.get("asset_holder")
        location = cleaned_data.get("location")

        if assign_to_type == 'holder' and not asset_holder:
            raise forms.ValidationError(
                "Please select an Asset Holder when assigning to a holder."
            )
        elif assign_to_type == 'location' and not location:
             raise forms.ValidationError(
                "Please select a Location when assigning to a location."
            )
        elif assign_to_type not in ['holder', 'location']:
            # This case might be redundant due to ChoiceField validation
             raise forms.ValidationError(
                "Please select whether to assign to a Holder or Location."
            )
        
        # Ensure only one target is selected based on the type
        if assign_to_type == 'holder' and location:
            self.add_error('location', "Clear the Location field when assigning to a Holder.")
        elif assign_to_type == 'location' and asset_holder:
            self.add_error('asset_holder', "Clear the Asset Holder field when assigning to a Location.")

        return cleaned_data 