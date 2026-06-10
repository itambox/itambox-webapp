from rest_framework import serializers
from itambox.api.base import BaseModelSerializer
from extras.models import Tag, Dashboard, CustomField, CustomFieldset


class TagSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:tag-detail')

    class Meta:
        model = Tag
        fields = [
            'id', 'url', 'name', 'slug', 'color',
            'description', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'name', 'slug', 'color']


class CustomFieldSerializer(BaseModelSerializer):
    field_type_display = serializers.CharField(source='get_field_type_display', read_only=True)

    object_types = serializers.SlugRelatedField(
        many=True, read_only=True, slug_field='model'
    )

    class Meta:
        model = CustomField
        fields = [
            'id', 'name', 'label', 'field_type', 'field_type_display',
            'choices', 'required', 'object_types', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'label', 'field_type']


class CustomFieldsetSerializer(BaseModelSerializer):
    fields = CustomFieldSerializer(many=True, read_only=True)

    class Meta:
        model = CustomFieldset
        fields = ['id', 'name', 'fields', 'created_at', 'updated_at']
        brief_fields = ['id', 'name']


class DashboardSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Dashboard
        fields = ['id', 'user', 'layout', 'created', 'last_updated']
        brief_fields = ['id', 'user']
