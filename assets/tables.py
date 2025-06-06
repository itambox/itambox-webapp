import django_tables2 as tables
from .models import Asset, AssetRole, Manufacturer # Updated import
from assetbox.core.tables import BaseTable # Absolute import

# Renamed from CategoryTable
class AssetRoleTable(BaseTable):
    # actions = tables.TemplateColumn(template_name='assets/includes/assetrole_actions.html') # Assuming actions partial needs renaming too
    class Meta(BaseTable.Meta):
        model = AssetRole # Updated model
        fields = ('name', 'slug', 'description')
        # Define default columns if needed, otherwise inherit from BaseTable
        default_columns = ['name', 'description']

# Table for Manufacturers
class ManufacturerTable(BaseTable):
    # actions = tables.TemplateColumn(template_name='assets/includes/manufacturer_actions.html')
    class Meta(BaseTable.Meta):
        model = Manufacturer
        fields = ('name', 'slug', 'description')
        default_columns = ['name', 'description']

# Table for Assets
class AssetTable(BaseTable):
    asset_role = tables.Column(linkify=True) # Updated field name, keep linkify
    manufacturer = tables.Column(linkify=True)
    location = tables.Column(linkify=True)
    # assigned_to = tables.Column(verbose_name="Assigned To") # Need custom logic for this
    # actions = tables.TemplateColumn(template_name='assets/includes/asset_actions.html')

    class Meta(BaseTable.Meta):
        model = Asset
        fields = (
            'name', 'asset_tag', 'serial_number', 'status', 'asset_role', # Updated field
            'manufacturer', 'location', 'purchase_date', 'warranty_end_date'
        )
        default_columns = [
            'name', 'asset_tag', 'serial_number', 'status', 'asset_role', 'manufacturer', 'location' # Updated field
        ]

    # def render_assigned_to(self, record):
    #     # Implement logic to display asset holder or location
    #     # based on AssetHolderAssignment or record.location
    #     return "Not Implemented" 