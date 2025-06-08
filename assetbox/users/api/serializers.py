from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import serializers
from ..models import UserPreference

User = get_user_model()

# Simplified User Serializer for now
class UserSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='users-api:user-detail')
    class Meta:
        model = User
        fields = ['id', 'url', 'username', 'first_name', 'last_name', 'email', 'is_staff', 'is_active', 'date_joined']

class GroupSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='users-api:group-detail')
    user_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Group
        fields = ['id', 'url', 'name', 'user_count']

class UserConfigSerializer(serializers.ModelSerializer):
    """Serializer for the UserPreference object, focusing on the data field."""
    class Meta:
        model = UserPreference
        fields = ['data']
        # For now, make it read-only until we implement update logic
        read_only_fields = ['data'] 