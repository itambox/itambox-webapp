from rest_framework import serializers

from assets.models import Asset, Supplier
from organization.models import Tenant, CostCenter
from itambox.api.base import BaseModelSerializer
from itambox.api.nested_serializers import NestedAssetSerializer
from organization.api.serializers import NestedTenantSerializer

from procurement.models import Contract


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
