from rest_framework import serializers
from django.contrib.auth import get_user_model
from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedAssetSerializer
from compliance.models import CustodyReceipt, AssetMaintenance, AuditSession, AssetAudit
from organization.api.serializers import AssetHolderSerializer, NestedLocationSerializer
from organization.models import Location
from assets.models import Asset, StatusLabel
from assets.api.serializers import StatusLabelSerializer

User = get_user_model()


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


class AuditSessionSerializer(BaseModelSerializer):
    location = NestedLocationSerializer(read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), source='location', write_only=True, required=False, allow_null=True
    )
    created_by = serializers.StringRelatedField(read_only=True)
    created_by_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='created_by', write_only=True, required=False
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = AuditSession
        fields = [
            'id', 'name', 'location', 'location_id', 'status', 'status_display',
            'started_at', 'completed_at', 'created_by', 'created_by_id',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'status', 'started_at']


class AssetAuditSerializer(serializers.ModelSerializer):
    session = serializers.PrimaryKeyRelatedField(
        queryset=AuditSession.objects.all(), required=False, allow_null=True
    )
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset'
    )
    auditor = serializers.StringRelatedField(read_only=True)
    location = NestedLocationSerializer(read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), source='location'
    )
    status = StatusLabelSerializer(read_only=True)
    status_id = serializers.PrimaryKeyRelatedField(
        queryset=StatusLabel.objects.all(), source='status'
    )
    verification_method_display = serializers.CharField(source='get_verification_method_display', read_only=True)

    class Meta:
        model = AssetAudit
        fields = [
            'id', 'session', 'asset', 'asset_id', 'auditor', 'timestamp',
            'location', 'location_id', 'status', 'status_id', 'notes',
            'verification_method', 'verification_method_display'
        ]
