from rest_framework import serializers
from extras.models import Tag

# Inspired by NetBox API serializers

class TagSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:tag-detail')

    class Meta:
        model = Tag
        fields = [
            'id', 'url', 'name', 'slug', 'color',
            'description', 'created_at', 'updated_at'
        ]
