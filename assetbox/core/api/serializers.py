# core/api/serializers.py
from rest_framework import serializers
from core.models import UserPreference

class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = ['data'] # Only expose the data field