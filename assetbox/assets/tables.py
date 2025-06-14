# assetbox/assets/tables.py
import django_tables2 as tables
from django_tables2.utils import A  # Alias for Accessor
from .models import Asset, AssetRole, Manufacturer, AssetType
from core.tables import ActionsColumn, BaseTable
from django.urls import reverse, NoReverseMatch
from django.utils.safestring import mark_safe
from django.contrib.contenttypes.models import ContentType
from organization.models import AssetHolderAssignment

class AssetTable(BaseTable): # Inherit from BaseTable
    pk = tables.CheckBoxColumn(accessor='pk', attrs = { "th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:asset_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(accessor='asset_type.manufacturer', linkify=True, verbose_name='Manufacturer')
    model = tables.Column(accessor='asset_type.model', linkify=True, verbose_name='Model')
    assignee = tables.Column(verbose_name='Assignee', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta): # Inherit Meta from BaseTable
        model = Asset
        fields = (
            'pk', 'name', 'asset_tag', 'serial_number', 'asset_type', 'asset_type__manufacturer', 'asset_type__model', 'asset_role', 
            'status', 'assignee', 'location', 'purchase_date', 'actions',
        )
        # Define default columns (adjust as desired)
        default_columns = (
            'pk', 'name', 'asset_tag', 'asset_type', 'asset_type__manufacturer', 'asset_type__model', 'asset_role', 
            'status', 'assignee', 'location', 'actions',
        )
        # Remove template_name and attrs, inherited from BaseTable

    def render_assignee(self, record):
        # Remove check for record.assigned_to
        # Directly check for AssetHolderAssignment
        assignment = AssetHolderAssignment.objects.filter(
            content_type=ContentType.objects.get_for_model(Asset),
            object_id=record.pk
        ).select_related('asset_holder').first()
        
        if assignment and assignment.asset_holder:
            holder = assignment.asset_holder
            try:
                # Link to asset holder detail view
                url = reverse('organization:assetholder_detail', kwargs={'pk': holder.pk})
                return mark_safe(f'<a href="{url}">{holder}</a>')
            except NoReverseMatch:
                # Fallback if detail view URL fails for some reason
                return str(holder)
        
        # If no assignment, check if there's a location
        if record.location:
             return f"Location: {record.location}" # Display location info
             
        return "—" # Em dash for empty/unassigned

    def render_serial_number(self, value):
        return value or "—"
        
    def render_asset_role(self, value):
        return value.name if value else "—"

    def render_location(self, value):
        return value.name if value else "—"

    def value_purchase_date(self, value):
        # Format date if it exists
        return value.strftime("%Y-%m-%d") if value else "—"

class AssetRoleTable(BaseTable): # Inherit from BaseTable
    pk = tables.CheckBoxColumn(accessor='pk', attrs = { "th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:assetrole_detail', args=[A('pk')], verbose_name='Name')
    color = tables.Column(verbose_name='Color', orderable=False)
    asset_count = tables.Column(verbose_name='Asset Count', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta): # Inherit Meta
        model = AssetRole
        fields = ('pk', 'name', 'color', 'description', 'asset_count', 'actions')
        default_columns = ('pk', 'name', 'color', 'asset_count', 'description', 'actions')

    def render_asset_count(self, record):
        # Calculate count dynamically or pass via annotation in view
        return record.asset_set.count() # Simple count query
        
    def render_color(self, value):
        if value:
            return mark_safe(f'<span class="badge" style="background-color: #{value};">&nbsp;</span> #{value}')
        return "—"

class ManufacturerTable(BaseTable): # Inherit from BaseTable
    pk = tables.CheckBoxColumn(accessor='pk', attrs = { "th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:manufacturer_detail', args=[A('pk')], verbose_name='Name')
    asset_count = tables.Column(verbose_name='Asset Count', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta): # Inherit Meta
        model = Manufacturer
        fields = ('pk', 'name', 'description', 'asset_count', 'actions')
        default_columns = ('pk', 'name', 'asset_count', 'description', 'actions')

    def render_asset_count(self, record):
        # Calculate count dynamically or pass via annotation in view
        return record.assets.count() # Use related_name 'assets'

class AssetTypeTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk')
    manufacturer = tables.Column(linkify=True) # Linkify using default get_absolute_url
    model = tables.LinkColumn('assets:assettype_detail', args=[A('slug')], verbose_name='Model')
    # TODO: Decide if asset count is needed here, could be expensive
    # asset_count = tables.Column(verbose_name='Instances', orderable=False)

    class Meta(BaseTable.Meta):
        model = AssetType
        fields = ('pk', 'manufacturer', 'model', 'part_number', 'actions')
        default_columns = ('pk', 'manufacturer', 'model', 'part_number', 'actions')

    # def render_asset_count(self, record):
    #    return Asset.objects.filter(asset_type=record).count() # Example if Asset links to AssetType
