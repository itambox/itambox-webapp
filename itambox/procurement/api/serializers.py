from rest_framework import serializers

from assets.models import Asset, Supplier, AssetType
from inventory.models import Component, Accessory, Consumable
from licenses.models import License
from organization.models import Tenant, CostCenter, Location
from itambox.api.base import BaseModelSerializer
from itambox.api.nested_serializers import NestedAssetSerializer, NestedAssetTypeSerializer
from inventory.api.serializers import (
    NestedComponentSerializer,
    NestedAccessorySerializer,
    NestedConsumableSerializer,
)
from organization.api.serializers import NestedTenantSerializer, NestedLocationSerializer

from procurement.models import Contract, PurchaseOrder, PurchaseOrderLine


class NestedSupplierSerializer(BaseModelSerializer):
    """Minimal nested representation of Supplier for read-only contract display."""

    class Meta:
        model = Supplier
        fields = ['id', 'name', 'slug']
        brief_fields = ['id', 'name', 'slug']


class ContractSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:procurement_api:contract-detail')

    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        source='tenant',
        write_only=True,
        required=False,
        allow_null=True,
        queryset=Tenant.objects,
    )

    supplier = NestedSupplierSerializer(read_only=True)
    supplier_id = serializers.PrimaryKeyRelatedField(
        source='supplier',
        write_only=True,
        required=False,
        allow_null=True,
        queryset=Supplier.objects.all(),
    )

    cost_center_display = serializers.SerializerMethodField(read_only=True)
    cost_center_id = serializers.PrimaryKeyRelatedField(
        source='cost_center', queryset=CostCenter.objects,
        write_only=True, required=False, allow_null=True,
    )

    assets = NestedAssetSerializer(many=True, read_only=True)
    assets_ids = serializers.PrimaryKeyRelatedField(
        source='assets',
        many=True,
        write_only=True,
        required=False,
        queryset=Asset.objects,
    )

    contract_type_display = serializers.CharField(source='get_contract_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    billing_cycle_display = serializers.CharField(source='get_billing_cycle_display', read_only=True)

    days_until_expiry = serializers.IntegerField(read_only=True)

    class Meta:
        model = Contract
        fields = [
            'id', 'url', 'display',
            'name', 'contract_number', 'contract_type', 'contract_type_display',
            'status', 'status_display',
            'tenant', 'tenant_id',
            'supplier', 'supplier_id',
            'cost', 'currency', 'billing_cycle', 'billing_cycle_display',
            'start_date', 'end_date', 'renewal_date', 'auto_renew',
            'sla_response_time', 'sla_resolution_time', 'coverage_hours', 'sla_terms',
            'assets', 'assets_ids',
            'purchase_order',
            'cost_center_display', 'cost_center_id',
            'notes',
            'days_until_expiry',
            'created_at', 'updated_at',
        ]
        brief_fields = [
            'id', 'url', 'display', 'name', 'contract_number', 'status', 'end_date',
        ]

    def get_cost_center_display(self, obj):
        cc = obj.cost_center
        if cc is None:
            return None
        return {'id': cc.pk, 'name': str(cc)}


class PurchaseOrderLineSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:procurement_api:purchaseorderline-detail')

    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        source='tenant',
        write_only=True,
        required=False,
        allow_null=True,
        queryset=Tenant.objects,
    )

    purchase_order = serializers.PrimaryKeyRelatedField(read_only=True)
    purchase_order_id = serializers.PrimaryKeyRelatedField(
        source='purchase_order',
        write_only=True,
        queryset=PurchaseOrder.objects,
    )

    asset_type = NestedAssetTypeSerializer(read_only=True)
    asset_type_id = serializers.PrimaryKeyRelatedField(
        source='asset_type', queryset=AssetType.objects,
        write_only=True, required=False, allow_null=True,
    )

    component = NestedComponentSerializer(read_only=True)
    component_id = serializers.PrimaryKeyRelatedField(
        source='component', queryset=Component.objects,
        write_only=True, required=False, allow_null=True,
    )

    accessory = NestedAccessorySerializer(read_only=True)
    accessory_id = serializers.PrimaryKeyRelatedField(
        source='accessory', queryset=Accessory.objects,
        write_only=True, required=False, allow_null=True,
    )

    consumable = NestedConsumableSerializer(read_only=True)
    consumable_id = serializers.PrimaryKeyRelatedField(
        source='consumable', queryset=Consumable.objects,
        write_only=True, required=False, allow_null=True,
    )

    license_display = serializers.SerializerMethodField(read_only=True)
    license_id = serializers.PrimaryKeyRelatedField(
        source='license', queryset=License.objects,
        write_only=True, required=False, allow_null=True,
    )

    qty_outstanding = serializers.IntegerField(read_only=True)
    total_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            'id', 'url', 'display',
            'tenant', 'tenant_id',
            'purchase_order', 'purchase_order_id',
            'asset_type', 'asset_type_id',
            'component', 'component_id',
            'accessory', 'accessory_id',
            'consumable', 'consumable_id',
            'license_display', 'license_id',
            'qty_ordered', 'qty_received', 'qty_outstanding',
            'unit_price', 'total_cost', 'currency',
            'created_at', 'updated_at',
        ]
        brief_fields = [
            'id', 'url', 'display', 'qty_ordered', 'qty_received',
        ]

    def get_license_display(self, obj):
        lic = obj.license
        if lic is None:
            return None
        return {'id': lic.pk, 'name': str(lic)}


class PurchaseOrderSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:procurement_api:purchaseorder-detail')

    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        source='tenant',
        write_only=True,
        required=False,
        allow_null=True,
        queryset=Tenant.objects,
    )

    supplier = NestedSupplierSerializer(read_only=True)
    supplier_id = serializers.PrimaryKeyRelatedField(
        source='supplier',
        write_only=True,
        queryset=Supplier.objects.all(),
    )

    destination_location = NestedLocationSerializer(read_only=True)
    destination_location_id = serializers.PrimaryKeyRelatedField(
        source='destination_location',
        write_only=True,
        queryset=Location.objects,
    )

    created_by_display = serializers.SerializerMethodField(read_only=True)

    status_display = serializers.CharField(source='get_status_display', read_only=True)

    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'url', 'display',
            'order_number', 'status', 'status_display',
            'tenant', 'tenant_id',
            'supplier', 'supplier_id',
            'currency',
            'order_date', 'expected_delivery_date',
            'destination_location', 'destination_location_id',
            'notes',
            'created_by_display',
            'lines',
            'created_at', 'updated_at',
        ]
        brief_fields = [
            'id', 'url', 'display', 'order_number', 'status',
        ]

    def get_created_by_display(self, obj):
        user = obj.created_by
        if user is None:
            return None
        return {'id': user.pk, 'name': str(user)}
