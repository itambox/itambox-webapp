from rest_framework import serializers
from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedAssetSerializer
from compliance.models import CustodyReceipt, AssetMaintenance
from organization.api.serializers import AssetHolderSerializer


class CustodyReceiptSerializer(BaseModelSerializer):
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedAssetSerializer.Meta.model.objects.all(),
        source='asset', write_only=True
    )
    holder = AssetHolderSerializer(read_only=True)
    holder_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetHolderSerializer.Meta.model.objects.all(),
        source='holder', write_only=True
    )
    acceptance_status_display = serializers.CharField(source='get_acceptance_status_display', read_only=True)

    class Meta:
        model = CustodyReceipt
        fields = [
            'id', 'asset', 'asset_id', 'holder', 'holder_id',
            'token', 'accepted', 'accepted_date', 'acceptance_method',
            'acceptance_status', 'acceptance_status_display',
            'signature_data', 'signature_hash', 'verification_hash',
            'signature_canvas', 'signed_at', 'eula_version', 'created_date',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'asset', 'holder', 'acceptance_status', 'signed_at']


class AssetMaintenanceSerializer(BaseModelSerializer):
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedAssetSerializer.Meta.model.objects.all(),
        source='asset', write_only=True
    )
    maintenance_type_display = serializers.CharField(source='get_maintenance_type_display', read_only=True)
    downtime_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssetMaintenance
        fields = [
            'id', 'asset', 'asset_id', 'supplier',
            'maintenance_type', 'maintenance_type_display',
            'cost', 'start_date', 'completion_date',
            'downtime_days', 'notes',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'asset', 'maintenance_type', 'start_date', 'cost']
