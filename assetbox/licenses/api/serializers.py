from rest_framework import serializers
from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedManufacturerSerializer, NestedAssetSerializer
from licenses.models import License, LicenseSeatAssignment
from extras.api.serializers import TagSerializer
from software.api.serializers import SoftwareSerializer
from organization.api.serializers import AssetHolderSerializer, NestedTenantSerializer


class LicenseSerializer(BaseModelSerializer):
    software = SoftwareSerializer(read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    available_seats = serializers.IntegerField(read_only=True)
    license_type_display = serializers.CharField(source='get_license_type_display', read_only=True)
    tenant = NestedTenantSerializer(read_only=True)

    class Meta:
        model = License
        fields = (
            'id', 'name', 'software', 'license_type', 'license_type_display', 'product_key',
            'seats', 'available_seats', 'purchase_date', 'purchase_cost',
            'order_number', 'expiration_date', 'notes', 'tags', 'tenant',
            'created_at', 'updated_at'
        )
        brief_fields = ['id', 'name', 'software', 'license_type', 'seats', 'available_seats']


class LicenseSeatAssignmentSerializer(BaseModelSerializer):
    license = LicenseSerializer(read_only=True)
    asset = NestedAssetSerializer(read_only=True)
    assigned_holder = AssetHolderSerializer(read_only=True)

    class Meta:
        model = LicenseSeatAssignment
        fields = (
            'id', 'license', 'asset', 'assigned_holder', 'assigned_date',
            'notes', 'created_at', 'updated_at'
        )
        brief_fields = ['id', 'license', 'asset']
