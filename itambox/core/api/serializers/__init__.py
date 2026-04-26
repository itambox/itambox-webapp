from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from core.api.base import BaseModelSerializer
from itambox.api.fields import ChoiceField, ContentTypeField
from core.models import ObjectChange
from core.choices import ObjectChangeActionChoices
from core.api.serializers.bulk import BulkOperationSerializer
from core.api.serializers.features import ChangeLogMessageSerializer

User = get_user_model()

__all__ = (
    'BulkOperationSerializer',
    'ChangeLogMessageSerializer',
    'ContentTypeSerializer',
    'NestedUserSerializer',
    'ObjectChangeSerializer',
)


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
