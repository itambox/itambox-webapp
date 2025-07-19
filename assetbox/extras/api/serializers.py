from rest_framework import serializers
from core.api.base import BaseModelSerializer
from extras.models import Tag


class TagSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:tag-detail')

    class Meta:
        model = Tag
        fields = [
            'id', 'url', 'name', 'slug', 'color',
            'description', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'name', 'slug', 'color']
