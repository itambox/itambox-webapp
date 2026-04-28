from rest_framework import serializers


class ChangeLogMessageSerializer(serializers.Serializer):
    changelog_message = serializers.CharField(required=False, allow_blank=True, write_only=True)
