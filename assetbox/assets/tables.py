# assetbox/assets/tables.py
import django_tables2 as tables
from django_tables2.utils import A  # Alias for Accessor
from .models import Asset, AssetRole, Manufacturer, AssetType
from core.tables import ActionsColumn, BaseTable
from extras.tables import TagColumn # Import TagColumn
from django.urls import reverse, NoReverseMatch
from django.utils.safestring import mark_safe
from django.contrib.contenttypes.models import ContentType
from organization.models import AssetHolderAssignment
from django.utils.html import format_html

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
        # *** Explicitly set default order_by for AssetTable ***
        order_by = ('name',)
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

class ManufacturerTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"onclick": "toggle(this)"}})
    name = tables.LinkColumn()
    asset_type_count = tables.Column(
        verbose_name='Asset Types',
        linkify=True,
        accessor='asset_types.count' # Directly access the count using the related name
    )
    asset_count = tables.Column(
        accessor='pk', # Use pk temporarily, will be replaced by render method
        verbose_name='Assets'
    )
    # description = tables.Column()
    # slug = tables.Column()
    tags = TagColumn(url_name='assets:manufacturer_list')
    actions = ActionsColumn()

    # Removed the problematic render_asset_count method
    # It's now handled by the asset_type_count column using accessor

    def render_asset_count(self, record):
        # This method counts Assets related via AssetType
        asset_count = Asset.objects.filter(asset_type__manufacturer=record).count()
        # TODO: Consider annotating this count in the view for performance.
        return asset_count

    def render_asset_type_count(self, value, record):
        # Customize the link for asset_type_count
        url = reverse('assets:assettype_list') + f'?manufacturer_id={record.pk}'
        return format_html('<a href="{}">{}</a>', url, value)

    class Meta(BaseTable.Meta):
        model = Manufacturer
        fields = (
            'pk', 'name', 'asset_type_count', 'asset_count', 'description', 'tags', 'actions'
        )
        default_columns = (
            'pk', 'name', 'asset_type_count', 'asset_count', 'description', 'actions'
        )

class AssetTypeTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk')
    manufacturer = tables.Column(linkify=True) # Linkify using default get_absolute_url
    model = tables.LinkColumn('assets:assettype_detail', args=[A('slug')], verbose_name='Model')
    created_at = tables.DateTimeColumn(format="Y-m-d") # Explicitly add 'created'
    last_updated = tables.DateTimeColumn(format="Y-m-d H:i") # Explicitly add 'last_updated'
    actions = ActionsColumn() # Add actions column

    class Meta(BaseTable.Meta):
        model = AssetType
        # Add 'created' and 'last_updated' to fields
        fields = ('pk', 'manufacturer', 'model', 'part_number', 'created', 'last_updated', 'actions') 
        # Keep default columns as before, or add created/last_updated if desired
        default_columns = ('pk', 'manufacturer', 'model', 'part_number', 'actions')
        # *** Explicitly set default order_by ***
        order_by = ('manufacturer', 'model')

    # def render_asset_count(self, record):
    #    return Asset.objects.filter(asset_type=record).count() # Example if Asset links to AssetType
