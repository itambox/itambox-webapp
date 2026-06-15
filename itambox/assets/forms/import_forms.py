from core.forms.import_forms import BulkImportForm, register_import_form
from assets.models import Asset, AssetType, Manufacturer
from inventory.models import Accessory, Consumable
from licenses.models import License
from organization.models import Location, AssetHolder


# These forms are declarative: the base BulkImportForm.map_row maps scalar
# columns and resolves ForeignKey columns (by id / slug / name) automatically.
# required_fields/optional_fields drive validation and the Field Options help
# table. Do not list non-model columns here — they are silently skipped.
# @register_import_form wires each form to the centralized GenericObjectImportView
# at /import/<app>/<model>/ (no per-app view needed).


@register_import_form
class AssetBulkImportForm(BulkImportForm):
    model = Asset
    # asset_tag is auto-generated when blank (AssetTagSequence), so it is optional.
    required_fields = ['name']
    optional_fields = ['asset_tag', 'serial_number', 'asset_type', 'asset_role',
                       'status', 'location', 'supplier', 'purchase_date',
                       'purchase_cost', 'order_number', 'notes']


@register_import_form
class AssetTypeBulkImportForm(BulkImportForm):
    model = AssetType
    required_fields = ['manufacturer', 'model']
    optional_fields = ['part_number', 'category', 'asset_role', 'description', 'comments']


@register_import_form
class ManufacturerBulkImportForm(BulkImportForm):
    model = Manufacturer
    required_fields = ['name']
    optional_fields = ['slug', 'description']


@register_import_form
class AccessoryBulkImportForm(BulkImportForm):
    model = Accessory
    required_fields = ['name', 'manufacturer']
    optional_fields = ['category', 'supplier', 'slug', 'part_number', 'min_qty', 'notes']


@register_import_form
class ConsumableBulkImportForm(BulkImportForm):
    model = Consumable
    required_fields = ['name', 'manufacturer']
    optional_fields = ['category', 'supplier', 'slug', 'part_number', 'min_qty', 'notes']


@register_import_form
class LicenseBulkImportForm(BulkImportForm):
    model = License
    required_fields = ['name', 'software']
    optional_fields = ['license_type', 'supplier', 'product_key', 'seats',
                       'purchase_date', 'purchase_cost', 'order_number',
                       'expiration_date', 'notes']


@register_import_form
class LocationBulkImportForm(BulkImportForm):
    model = Location
    required_fields = ['name', 'site']
    optional_fields = ['slug', 'status', 'parent', 'facility', 'description']


@register_import_form
class AssetHolderBulkImportForm(BulkImportForm):
    model = AssetHolder
    required_fields = ['first_name', 'last_name', 'upn']
    optional_fields = ['email', 'description', 'comments']
