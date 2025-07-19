from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedManufacturerSerializer
from extras.api.serializers import TagSerializer
from software.models import Software


class SoftwareSerializer(BaseModelSerializer):
    manufacturer = NestedManufacturerSerializer(read_only=True)
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = Software
        fields = (
            'id', 'name', 'manufacturer', 'description', 'tags',
            'created_at', 'updated_at'
        )
        brief_fields = ['id', 'name', 'manufacturer']
