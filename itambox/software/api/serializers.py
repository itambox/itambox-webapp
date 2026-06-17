from rest_framework import serializers

from assets.models import Manufacturer, Asset
from itambox.api.base import BaseModelSerializer
from itambox.api.nested_serializers import NestedManufacturerSerializer, NestedAssetSerializer
from extras.api.serializers import TagSerializer
from organization.api.serializers import NestedTenantSerializer
from organization.models import Tenant
from software.models import Software, InstalledSoftware


class SoftwareSerializer(BaseModelSerializer):
    """Serializer for the Software model.

    This serializer handles full representation of the Software model, exposing
    manufacturer relationships through a nested serializer for read operations and a
    write-only primary key field for creation and update actions.

    Attributes:
        manufacturer (NestedManufacturerSerializer): The manufacturer details (read-only).
        manufacturer_id (PrimaryKeyRelatedField): Writable reference to the manufacturer.
        tags (TagSerializer): Associated tags for the software (read-only).
    """

    manufacturer: NestedManufacturerSerializer = NestedManufacturerSerializer(read_only=True)
    manufacturer_id: serializers.PrimaryKeyRelatedField = serializers.PrimaryKeyRelatedField(
        queryset=Manufacturer.objects.all(),
        source='manufacturer',
        write_only=True,
        help_text="The ID of the manufacturer for this software"
    )
    tags: TagSerializer = TagSerializer(many=True, read_only=True)
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects, source='tenant', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Software
        fields = (
            'id',
            'name',
            'manufacturer',
            'manufacturer_id',
            'tenant',
            'tenant_id',
            'version',
            'category',
            'license_type',
            'website',
            'description',
            'tags',
            'created_at',
            'updated_at',
        )
        brief_fields = ['id', 'name', 'manufacturer']


class InstalledSoftwareSerializer(BaseModelSerializer):
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects, source='asset', write_only=True
    )
    software_id = serializers.PrimaryKeyRelatedField(
        queryset=Software.objects, source='software', write_only=True
    )
    software_name = serializers.CharField(source='software.name', read_only=True)
    software_manufacturer = serializers.CharField(source='software.manufacturer.name', read_only=True)

    class Meta:
        model = InstalledSoftware
        fields = (
            'id', 'asset', 'asset_id', 'software', 'software_id', 'software_name', 'software_manufacturer',
            'version_detected', 'install_date', 'discovered_by_agent',
            'last_seen_date', 'notes',
            'created_at', 'updated_at'
        )
        read_only_fields = ('created_at', 'updated_at')
        brief_fields = ['id', 'software_name', 'version_detected']
