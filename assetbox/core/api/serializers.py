# core/api/serializers.py
from rest_framework import serializers
from core.models import UserPreference
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from core.models import ObjectChange
from core.choices import ObjectChangeActionChoices
from .fields import ChoiceField, ContentTypeField

User = get_user_model()

class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = ['data'] # Only expose the data field

# Minimal serializer for nested User representation
class NestedUserSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='users-api:user-detail') # Adjust view_name if needed

    class Meta:
        model = User
        fields = ['id', 'url', 'username']

class ObjectChangeSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='core-api:objectchange-detail') # Assuming this view name
    user = NestedUserSerializer(read_only=True)
    action = ChoiceField(choices=ObjectChangeActionChoices(), read_only=True)
    changed_object_type = ContentTypeField(read_only=True)
    changed_object = serializers.SerializerMethodField(read_only=True)
    display = serializers.SerializerMethodField(read_only=True) # For a user-friendly display string

    class Meta:
        model = ObjectChange
        fields = [
            'id', 'url', 'display', 'time', 'user', 'user_name', 'request_id', 'action',
            'changed_object_type', 'changed_object_id', 'changed_object',
            'prechange_data', 'postchange_data', 'object_repr', 'object_type_repr',
        ]

    def get_display(self, obj):
        # Replicate the __str__ logic or create a simpler display
        action_label = obj.get_action_display()
        user_display = obj.user.username if obj.user else obj.user_name
        return f"{obj.object_type_repr or obj.changed_object_type}: {obj.object_repr} {action_label} by {user_display}"

    def get_changed_object(self, obj):
        # Provide basic info. A more complex approach could return a nested object with its own URL.
        if obj.changed_object is None:
            return None
        return {
            'id': obj.changed_object_id,
            # Attempt to get an API URL if the object's serializer provides one, otherwise None
            # 'url': getattr(obj.changed_object, 'get_api_url', lambda: None)(), # Requires get_api_url on models
            'object_type': str(obj.changed_object_type),
            'display': obj.object_repr, # Use the stored representation
        }