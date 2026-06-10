from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import serializers

from itambox.api.base import BaseModelSerializer
from users.models import UserPreference, Token

User = get_user_model()


class UserSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:users_api:user-detail')

    class Meta:
        model = User
        fields = ['id', 'url', 'username', 'first_name', 'last_name', 'email', 'is_staff', 'is_active', 'date_joined']
        brief_fields = ['id', 'url', 'username']


class GroupSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:users_api:group-detail')
    user_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Group
        fields = ['id', 'url', 'name', 'user_count']
        brief_fields = ['id', 'url', 'name']


class UserConfigSerializer(BaseModelSerializer):
    class Meta:
        model = UserPreference
        fields = ['data']
        read_only_fields = ['data']
        brief_fields = ['data']


class TokenSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='user', write_only=True
    )

    class Meta:
        model = Token
        fields = [
            'id', 'key', 'user', 'user_id', 'created',
            'expires', 'last_used', 'write_enabled', 'description'
        ]
        read_only_fields = ['key', 'created', 'last_used']
        brief_fields = ['id', 'key', 'user', 'created']
