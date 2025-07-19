from django.contrib.contenttypes.models import ContentType
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.api.fields import ContentTypeField
from core.api.utils import get_serializer_for_model
from core.api.gfk_fields import GFKSerializerField

__all__ = (
    'GenericObjectSerializer',
    'GFKSerializerField',
)


class GenericObjectSerializer(serializers.Serializer):
    object_type = ContentTypeField(
        queryset=ContentType.objects.all()
    )
    object_id = serializers.IntegerField()
    object = GFKSerializerField(read_only=True)

    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        model = data['object_type'].model_class()
        return model.objects.get(pk=data['object_id'])

    def to_representation(self, instance):
        ct = ContentType.objects.get_for_model(instance)
        return {
            'object_type': f"{ct.app_label}.{ct.model}",
            'object_id': instance.pk,
            'object': GFKSerializerField().to_representation(instance) if 'request' in self.context else None,
        }
