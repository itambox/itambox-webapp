from rest_framework import serializers

from assets.models import Manufacturer
from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedManufacturerSerializer
from extras.api.serializers import TagSerializer
from software.models import Software


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

    class Meta:
        model = Software
        fields = (
            'id',
            'name',
            'manufacturer',
            'manufacturer_id',
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
