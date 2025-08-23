from rest_framework import serializers


class BulkOperationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    changelog_message = serializers.CharField(required=False, allow_blank=True, write_only=True)
