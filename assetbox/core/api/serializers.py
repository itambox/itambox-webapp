# core/api/serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from core.models import ObjectChange
from core.choices import ObjectChangeActionChoices
from .fields import ChoiceField, ContentTypeField

User = get_user_model()

# --- ContentType Serializer --- 

class ContentTypeSerializer(serializers.ModelSerializer):
    """Basic serializer for ContentType model."""
    url = serializers.HyperlinkedIdentityField(view_name='core-api:contenttype-detail') # Placeholder view name

    class Meta:
        model = ContentType
        fields = ['id', 'url', 'app_label', 'model']

# --- Nested User Serializer --- 

# Minimal serializer for nested User representation
class NestedUserSerializer(serializers.ModelSerializer):
    # url = serializers.HyperlinkedIdentityField(view_name='users_api:user-detail') # Adjust view_name if needed

    class Meta:
        model = User
        fields = ['id', 'username'] # Keep it minimal for nesting

# --- ObjectChange Serializer --- 

class ObjectChangeSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='core_api:objectchange-detail') # Use underscore
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

# --- Generic Object Serializer (for GFKs) --- 

class GenericObjectSerializer(serializers.Serializer):
    """
    Simple serializer for representing related objects via GenericForeignKey.
    Adjust as needed based on desired output for GFK relations.
    """
    object_type = serializers.CharField(source='content_type.model', read_only=True)
    object_id = serializers.IntegerField(source='pk', read_only=True)
    display = serializers.CharField(source='__str__', read_only=True)
    # Add url if your models have a get_api_url() method or similar
    # url = serializers.SerializerMethodField()
    
    # def get_url(self, obj):
    #     if hasattr(obj, 'get_api_url'):
    #         return obj.get_api_url()
    #     return None