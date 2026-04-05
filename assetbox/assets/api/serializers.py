from django.contrib.auth import get_user_model
from rest_framework import serializers
from core.api.base import BaseModelSerializer
from assetbox.api.fields import RelatedObjectCountField
from core.api.nested_serializers import (
    NestedAssetRoleSerializer,
    NestedManufacturerSerializer,
    NestedAssetSerializer,
    NestedAssetTypeSerializer
)
from assets.models import (
    Asset, AssetRole, Manufacturer, AssetType, InstalledSoftware,
    StatusLabel, Depreciation, Supplier, Category, AssetRequest, AssetTagSequence,
    AssetAssignment, AuditSession, AssetAudit
)
from organization.models import Location, Tenant
from software.models import Software
from organization.api.serializers import NestedLocationSerializer, NestedTenantSerializer
from extras.api.serializers import TagSerializer

User = get_user_model()


class AssetRoleSerializer(BaseModelSerializer):
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssetRole
        fields = ['id', 'name', 'slug', 'description', 'color', 'asset_count', 'created_at', 'updated_at']
        brief_fields = ['id', 'name', 'slug', 'color']


class ManufacturerSerializer(BaseModelSerializer):
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Manufacturer
        fields = ['id', 'name', 'slug', 'description', 'asset_count', 'created_at', 'updated_at']
        brief_fields = ['id', 'name', 'slug']


class StatusLabelSerializer(BaseModelSerializer):
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = StatusLabel
        fields = [
            'id', 'name', 'slug', 'type', 'type_display',
            'description', 'color', 'tags',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'slug', 'type', 'color']


class DepreciationSerializer(BaseModelSerializer):
    class Meta:
        model = Depreciation
        fields = ['id', 'name', 'months', 'created_at', 'updated_at']
        brief_fields = ['id', 'name', 'months']


class AssetTypeSerializer(BaseModelSerializer):
    manufacturer = NestedManufacturerSerializer(read_only=True)
    manufacturer_id = serializers.PrimaryKeyRelatedField(
        queryset=Manufacturer.objects.all(), source='manufacturer',
        write_only=True
    )
    tags = TagSerializer(many=True, read_only=True)
    depreciation = DepreciationSerializer(read_only=True)
    depreciation_id = serializers.PrimaryKeyRelatedField(
        queryset=Depreciation.objects.all(), source='depreciation',
        write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = AssetType
        fields = [
            'id', 'model', 'slug', 'manufacturer', 'manufacturer_id',
            'part_number', 'eol_months', 'category', 'depreciation',
            'depreciation_id', 'custom_fieldset', 'custom_values',
            'image', 'requestable', 'description', 'comments',
            'tags', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'model', 'slug', 'manufacturer']


class AssetSerializer(BaseModelSerializer):
    asset_type = NestedAssetTypeSerializer(read_only=True)
    asset_type_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetType.objects.all(), source='asset_type', write_only=True
    )
    asset_role = NestedAssetRoleSerializer(read_only=True)
    assetrole_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetRole.objects.all(), source='asset_role', write_only=True, required=False, allow_null=True
    )
    location = NestedLocationSerializer(read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), source='location', write_only=True, required=False, allow_null=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(), source='tenant', write_only=True, required=False, allow_null=True
    )
    supplier = serializers.StringRelatedField(read_only=True)
    supplier_id = serializers.PrimaryKeyRelatedField(
        queryset=Supplier.objects.all(), source='supplier',
        write_only=True, required=False, allow_null=True
    )
    status = StatusLabelSerializer(read_only=True)
    status_id = serializers.PrimaryKeyRelatedField(
        queryset=StatusLabel.objects.all(), source='status',
        write_only=True, required=False, allow_null=True
    )
    last_audited_by = serializers.StringRelatedField(read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    assigned_to = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            'id', 'name', 'asset_tag', 'serial_number', 'status', 'status_id',
            'asset_type', 'asset_type_id', 'asset_role', 'assetrole_id',
            'location', 'location_id', 'tenant', 'tenant_id',
            'purchase_date', 'warranty_expiration',
            'purchase_cost', 'order_number', 'supplier', 'supplier_id',
            'salvage_value', 'last_audited', 'last_audited_by',
            'custom_values', 'requestable',
            'notes', 'tags', 'assigned_to', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'asset_tag', 'serial_number', 'status']

    def get_assigned_to(self, obj):
        cached = getattr(obj, '_active_assignments', None)
        if cached is not None:
            active = cached[0] if cached else None
        else:
            active = obj.active_assignment
        if not active:
            return None
        target = active.assigned_to
        if target is None:
            return None
        return {
            'id': target.pk,
            'type': active.assigned_to_type,
            'name': str(target),
            'is_active': active.is_active,
            'checked_out_at': active.checked_out_at,
            'expected_checkin_date': active.expected_checkin_date,
        }


class InstalledSoftwareSerializer(BaseModelSerializer):
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True
    )
    software_id = serializers.PrimaryKeyRelatedField(
        queryset=Software.objects.all(), source='software', write_only=True
    )
    software_name = serializers.CharField(source='software.name', read_only=True)
    software_manufacturer = serializers.CharField(source='software.manufacturer.name', read_only=True)

    class Meta:
        model = InstalledSoftware
        fields = (
            'id', 'asset', 'asset_id', 'software', 'software_id', 'software_name', 'software_manufacturer',
            'version_detected', 'install_date', 'discovered_by_agent',
            'last_seen_date', 'notes',
            'created_at', 'updated_at'
        )
        read_only_fields = ('created_at', 'updated_at')
        brief_fields = ['id', 'software_name', 'version_detected']




class SupplierSerializer(BaseModelSerializer):
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = Supplier
        fields = [
            'id', 'name', 'slug', 'website', 'contact_email',
            'contact_phone', 'address', 'contact_name', 'notes', 'tags',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'slug']


class CategorySerializer(BaseModelSerializer):
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = Category
        fields = [
            'id', 'name', 'slug', 'color', 'description',
            'email_on_checkout', 'email_on_checkin', 'require_acceptance',
            'email_eula', 'applies_to', 'tags',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'slug', 'color']


class AssetRequestSerializer(BaseModelSerializer):
    requester = serializers.StringRelatedField(read_only=True)
    requester_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='requester', write_only=True
    )
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True, required=False, allow_null=True
    )
    asset_type = NestedAssetTypeSerializer(read_only=True)
    asset_type_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetType.objects.all(), source='asset_type', write_only=True, required=False, allow_null=True
    )
    responded_by = serializers.StringRelatedField(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = AssetRequest
        fields = [
            'id', 'requester', 'requester_id',
            'asset', 'asset_id', 'asset_type', 'asset_type_id',
            'status', 'status_display', 'request_date', 'response_date',
            'responded_by', 'notes', 'response_notes', 'tags',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'requester', 'asset', 'status', 'request_date']


class AssetTagSequenceSerializer(BaseModelSerializer):
    class Meta:
        model = AssetTagSequence
        fields = ['id', 'prefix', 'next_value', 'zero_padding', 'created_at', 'updated_at']
        brief_fields = ['id', 'prefix', 'next_value']



class AssetAssignmentSerializer(BaseModelSerializer):
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True
    )
    assigned_to_type = serializers.CharField(read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    checked_out_by_name = serializers.CharField(source='checked_out_by.username', read_only=True)

    class Meta:
        model = AssetAssignment
        fields = [
            'id', 'asset', 'asset_id', 'assigned_user', 'assigned_location', 'assigned_asset',
            'assigned_to_type', 'assigned_to_name',
            'checked_out_by', 'checked_out_by_name', 'checked_out_at',
            'expected_checkin_date', 'is_active', 'checked_in_at',
            'checked_in_by', 'notes', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'asset', 'assigned_to_name', 'is_active']

    def get_assigned_to_name(self, obj):
        try:
            return str(obj.assigned_to)
        except Exception:
            return None


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


class AssetCheckOutAPISerializer(serializers.Serializer):
    holder_id = serializers.IntegerField(required=False, allow_null=True)
    location_id = serializers.IntegerField(required=False, allow_null=True)
    asset_target_id = serializers.IntegerField(required=False, allow_null=True)
    expected_checkin = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, default='', allow_blank=True)

    def validate(self, data):
        holder_id = data.get('holder_id')
        location_id = data.get('location_id')
        asset_target_id = data.get('asset_target_id')
        
        # Validate that exactly one assignment target is specified
        targets = [holder_id, location_id, asset_target_id]
        filled = [t for t in targets if t is not None]
        
        if not filled:
            raise serializers.ValidationError("Either holder_id, location_id, or asset_target_id must be provided.")
        if len(filled) > 1:
            raise serializers.ValidationError("You can only check out an asset to ONE target.")

        from organization.models import AssetHolder, Location
        from assets.models import Asset

        # Resolve entity references
        if holder_id:
            try:
                data['holder'] = AssetHolder.objects.get(pk=holder_id)
            except AssetHolder.DoesNotExist:
                raise serializers.ValidationError({"holder_id": "Specified holder does not exist."})
        elif location_id:
            try:
                data['location'] = Location.objects.get(pk=location_id)
            except Location.DoesNotExist:
                raise serializers.ValidationError({"location_id": "Specified location does not exist."})
        elif asset_target_id:
            try:
                data['asset_target'] = Asset.objects.get(pk=asset_target_id)
            except Asset.DoesNotExist:
                raise serializers.ValidationError({"asset_target_id": "Specified target asset does not exist."})
                
        return data


