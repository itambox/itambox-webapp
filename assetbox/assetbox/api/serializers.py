from django.contrib.contenttypes.models import ContentType
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

# Assuming ObjectType is not used, directly use ContentType
# from core.models import ObjectType
from .fields import ContentTypeField
# Utility function to get serializer based on model - needs to be created or imported
# from utilities.api import get_serializer_for_model

__all__ = (
    'GenericObjectSerializer',
)

# Placeholder function - replace with actual implementation or import
def get_serializer_for_model(model, prefix=''):
    # This needs to dynamically find the appropriate API serializer class
    # based on the model instance passed to it.
    # For now, return a basic dict serializer as a placeholder.
    # See NetBox's utilities.api for a real implementation.
    print(f"WARNING: get_serializer_for_model not fully implemented for {model}")
    class BasicObjectSerializer(serializers.Serializer):
        id = serializers.IntegerField()
        name = serializers.CharField(required=False) # Common field
        def to_representation(self, instance):
            return {'id': instance.id, 'name': str(instance)}
    return BasicObjectSerializer

class GenericObjectSerializer(serializers.Serializer):
    """
    Minimal representation of some generic object identified by ContentType and PK.
    Based on NetBox implementation.
    """
    object_type = ContentTypeField(
        queryset=ContentType.objects.all()
    )
    object_id = serializers.IntegerField()
    object = serializers.SerializerMethodField(read_only=True)

    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        model = data['object_type'].model_class()
        return model.objects.get(pk=data['object_id'])

    def to_representation(self, instance):
        ct = ContentType.objects.get_for_model(instance)
        data = {
            'object_type': f"{ct.app_label}.{ct.model}",
            'object_id': instance.pk,
        }
        if 'request' in self.context:
            data['object'] = self.get_object(instance)

        return data

    @extend_schema_field(serializers.JSONField(allow_null=True))
    def get_object(self, obj):
        # This requires a utility function like NetBox's get_serializer_for_model
        # to dynamically determine the correct serializer for the related object.
        serializer_class = get_serializer_for_model(obj) # Use placeholder for now
        if serializer_class is None:
            return None
        # Pass nested=True to get the brief representation
        serializer = serializer_class(obj, nested=True, context=self.context)
        return serializer.data 