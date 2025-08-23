from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from assetbox.api.base import BaseModelSerializer
from assetbox.api.fields import ChoiceField, ContentTypeField
from assetbox.api.gfk_fields import GFKSerializerField
from core.models import ObjectChange
from core.choices import ObjectChangeActionChoices
from assetbox.api.serializers.bulk import BulkOperationSerializer
from assetbox.api.serializers.features import ChangeLogMessageSerializer

User = get_user_model()

from drf_spectacular.utils import extend_schema_field


__all__ = (
    'BulkOperationSerializer',
    'ChangeLogMessageSerializer',
    'ContentTypeSerializer',
    'GenericObjectSerializer',
    'GFKSerializerField',
    'NestedUserSerializer',
    'ObjectChangeSerializer',
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


class ContentTypeSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:core_api:contenttype-detail')

    class Meta:
        model = ContentType
        fields = ['id', 'url', 'app_label', 'model']


class NestedUserSerializer(BaseModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username']
        brief_fields = ['id', 'username']


class ObjectChangeSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:core_api:objectchange-detail')
    user = NestedUserSerializer(read_only=True)
    action = ChoiceField(choices=ObjectChangeActionChoices(), read_only=True)
    changed_object_type = ContentTypeField(read_only=True)
    changed_object = serializers.SerializerMethodField(read_only=True)
    display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ObjectChange
        fields = [
            'id', 'url', 'display', 'time', 'user', 'user_name', 'request_id', 'action',
            'changed_object_type', 'changed_object_id', 'changed_object',
            'prechange_data', 'postchange_data', 'object_repr', 'object_type_repr',
        ]
        brief_fields = ['id', 'url', 'display', 'time', 'user', 'action', 'object_repr']

    def get_display(self, obj):
        action_label = obj.get_action_display()
        user_display = obj.user.username if obj.user else obj.user_name
        return f"{obj.object_type_repr or obj.changed_object_type}: {obj.object_repr} {action_label} by {user_display}"

    def get_changed_object(self, obj):
        if obj.changed_object is None:
            return None
        return {
            'id': obj.changed_object_id,
            'object_type': str(obj.changed_object_type),
            'display': obj.object_repr,
        }
