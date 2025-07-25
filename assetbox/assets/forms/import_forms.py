from core.forms.import_forms import BulkImportForm
from assets.models import Asset, AssetType, Manufacturer, Accessory, Consumable
from licenses.models import License
from organization.models import Location, AssetHolder


class AssetBulkImportForm(BulkImportForm):
    model = Asset
    required_fields = ['name', 'asset_tag']
    optional_fields = ['serial_number', 'description', 'purchase_date',
                       'purchase_cost', 'order_number', 'notes']

    def map_row(self, row):
        return {k: row.get(k, '').strip() for k in self.field_names}


class AssetTypeBulkImportForm(BulkImportForm):
    model = AssetType
    required_fields = ['manufacturer', 'model']
    optional_fields = ['part_number', 'cpu', 'ram_gb', 'storage_capacity_gb',
                       'storage_type', 'gpu', 'description', 'comments']

    def map_row(self, row):
        return {k: row.get(k, '').strip() for k in self.field_names}


class ManufacturerBulkImportForm(BulkImportForm):
    model = Manufacturer
    required_fields = ['name']
    optional_fields = ['slug', 'description']

    def map_row(self, row):
        return {k: row.get(k, '').strip() for k in self.field_names}


class AccessoryBulkImportForm(BulkImportForm):
    model = Accessory
    required_fields = ['name', 'manufacturer']
    optional_fields = ['category', 'slug', 'part_number', 'qty', 'min_qty',
                       'notes']

    def map_row(self, row):
        return {k: row.get(k, '').strip() for k in self.field_names}


class ConsumableBulkImportForm(BulkImportForm):
    model = Consumable
    required_fields = ['name', 'manufacturer']
    optional_fields = ['category', 'slug', 'part_number', 'qty', 'min_qty',
                       'notes']

    def map_row(self, row):
        return {k: row.get(k, '').strip() for k in self.field_names}


class LicenseBulkImportForm(BulkImportForm):
    model = License
    required_fields = ['name', 'software']
    optional_fields = ['license_type', 'product_key', 'seats', 'purchase_date',
                       'purchase_cost', 'order_number', 'expiration_date',
                       'notes']

    def map_row(self, row):
        return {k: row.get(k, '').strip() for k in self.field_names}


class LocationBulkImportForm(BulkImportForm):
    model = Location
    required_fields = ['name', 'site']
    optional_fields = ['slug', 'status', 'parent', 'facility', 'description']

    def map_row(self, row):
        return {k: row.get(k, '').strip() for k in self.field_names}


class AssetHolderBulkImportForm(BulkImportForm):
    model = AssetHolder
    required_fields = ['first_name', 'last_name', 'upn']
    optional_fields = ['email', 'description', 'comments']

    def map_row(self, row):
        return {k: row.get(k, '').strip() for k in self.field_names}
