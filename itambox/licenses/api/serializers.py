from rest_framework import serializers
from itambox.api.base import BaseModelSerializer
from itambox.api.nested_serializers import NestedManufacturerSerializer, NestedAssetSerializer
from licenses.models import License, LicenseSeatAssignment
from extras.api.serializers import TagSerializer
from software.api.serializers import SoftwareSerializer
from organization.api.serializers import AssetHolderSerializer, NestedTenantSerializer
from software.models import Software, InstalledSoftware
from subscriptions.models import Subscription
from organization.models import Tenant, AssetHolder, CostCenter
from assets.models import Asset


class LicenseSerializer(BaseModelSerializer):
    software = SoftwareSerializer(read_only=True)
    software_id = serializers.PrimaryKeyRelatedField(
        queryset=Software.objects.all(), source='software', write_only=True
    )
    tags = TagSerializer(many=True, read_only=True)
    available_seats = serializers.IntegerField(read_only=True)
    license_type_display = serializers.CharField(source='get_license_type_display', read_only=True)
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(), source='tenant', write_only=True, required=False, allow_null=True
    )
    subscription = serializers.StringRelatedField(read_only=True)
    # Subscription.objects is tenant-scoped, so a license cannot be funded by
    # another tenant's subscription.
    subscription_id = serializers.PrimaryKeyRelatedField(
        queryset=Subscription.objects.all(), source='subscription', write_only=True, required=False, allow_null=True
    )
    cost_center = serializers.StringRelatedField(read_only=True)
    cost_center_id = serializers.PrimaryKeyRelatedField(
        source='cost_center', queryset=CostCenter.objects.all(),
        write_only=True, required=False, allow_null=True,
    )

    class Meta:
        model = License
        fields = (
            'id', 'name', 'software', 'software_id', 'license_type', 'license_type_display', 'product_key',
            'seats', 'available_seats', 'purchase_date', 'purchase_cost', 'currency',
            'order_number', 'version', 'expiration_date', 'notes', 'tags', 'tenant', 'tenant_id',
            'subscription', 'subscription_id',
            'cost_center', 'cost_center_id',
            'created_at', 'updated_at'
        )
        brief_fields = ['id', 'name', 'software', 'license_type', 'seats', 'available_seats']


class LicenseSeatAssignmentSerializer(BaseModelSerializer):
    license = LicenseSerializer(read_only=True)
    license_id = serializers.PrimaryKeyRelatedField(
        queryset=License.objects.all(), source='license', write_only=True
    )
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True, required=False, allow_null=True
    )
    assigned_holder = AssetHolderSerializer(read_only=True)
    assigned_holder_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetHolder.objects.all(), source='assigned_holder', write_only=True, required=False, allow_null=True
    )
    # Optional precise install link (seat-level SAM).  Read: nested string repr;
    # write: bare PK via installed_software_id.
    installed_software = serializers.StringRelatedField(read_only=True)
    installed_software_id = serializers.PrimaryKeyRelatedField(
        queryset=InstalledSoftware.objects.all(),
        source='installed_software',
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = LicenseSeatAssignment
        fields = (
            'id', 'license', 'license_id', 'asset', 'asset_id', 'assigned_holder', 'assigned_holder_id',
            'installed_software', 'installed_software_id',
            'assigned_date', 'notes', 'created_at', 'updated_at'
        )
        brief_fields = ['id', 'license', 'asset']

