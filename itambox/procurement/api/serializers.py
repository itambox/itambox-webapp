from rest_framework import serializers

from assets.models import Asset, Supplier
from organization.models import Tenant
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
        queryset=Tenant.objects.all(),
    )

    supplier = NestedSupplierSerializer(read_only=True)
    supplier_id = serializers.PrimaryKeyRelatedField(
        source='supplier',
        write_only=True,
        required=False,
        allow_null=True,
        queryset=Supplier.objects.all(),
    )

    # cost_center: CostCenter is being created by another agent concurrently.
    # Expose a read-only display field now; the write field (cost_center_id) is
    # added dynamically in __init__ once we know the model exists, so importing
    # this serializer before the CostCenter migration lands is safe.
    cost_center_display = serializers.SerializerMethodField(read_only=True)

    assets = NestedAssetSerializer(many=True, read_only=True)
    assets_ids = serializers.PrimaryKeyRelatedField(
        source='assets',
        many=True,
        write_only=True,
        required=False,
        queryset=Asset.objects.all(),
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
            'cost_center_display',
            'notes',
            'days_until_expiry',
            'created_at', 'updated_at',
        ]
        brief_fields = [
            'id', 'url', 'display', 'name', 'contract_number', 'status', 'end_date',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dynamically add the cost_center_id write field once CostCenter exists.
        # This avoids the DRF assertion (queryset must not be None) at class-body
        # evaluation time while still supporting writes after the migration lands.
        try:
            from django.apps import apps
            CostCenter = apps.get_model('organization', 'CostCenter')
            self.fields['cost_center_id'] = serializers.PrimaryKeyRelatedField(
                source='cost_center',
                write_only=True,
                required=False,
                allow_null=True,
                queryset=CostCenter.objects.all(),
            )
        except LookupError:
            # CostCenter not yet registered — skip the write field silently.
            pass

    def get_cost_center_display(self, obj):
        cc = obj.cost_center
        if cc is None:
            return None
        return {'id': cc.pk, 'name': str(cc)}
