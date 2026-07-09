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
        fields = ['id', 'url', 'username', 'first_name', 'last_name', 'email', 'is_staff', 'is_active', 'can_login', 'date_joined']
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
    # The plaintext key is only present on the response to the CREATE call
    # (shown once); subsequent reads return null. `key_preview` is the
    # non-secret identifier shown in listings.
    key = serializers.ReadOnlyField()

    class Meta:
        model = Token
        fields = [
            'id', 'key', 'key_preview', 'user', 'user_id', 'created',
            'expires', 'last_used', 'write_enabled', 'allowed_ips', 'description'
        ]
        read_only_fields = ['key_preview', 'created', 'last_used']
        brief_fields = ['id', 'key_preview', 'user', 'created']
