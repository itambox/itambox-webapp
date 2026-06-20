from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from itambox.api.base import BaseModelSerializer
from itambox.api.nested_serializers import NestedManufacturerSerializer, NestedAssetSerializer
from licenses.models import License, LicenseSeatAssignment
from extras.api.serializers import TagSerializer
from software.api.serializers import SoftwareSerializer
from organization.api.serializers import AssetHolderSerializer, NestedTenantSerializer
from software.models import Software, InstalledSoftware
from subscriptions.models import Subscription
from organization.models import Tenant, AssetHolder, CostCenter
from assets.models import Asset


class LicenseSerializer(BaseModelSerializer):
    software = SoftwareSerializer(read_only=True)
    software_id = serializers.PrimaryKeyRelatedField(
        queryset=Software.objects, source='software', write_only=True
    )
    tags = TagSerializer(many=True, read_only=True)
    available_seats = serializers.IntegerField(read_only=True)
    license_type_display = serializers.CharField(source='get_license_type_display', read_only=True)
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects, source='tenant', write_only=True, required=False, allow_null=True
    )
    subscription = serializers.StringRelatedField(read_only=True)
    # Subscription.objects is tenant-scoped, so a license cannot be funded by
    # another tenant's subscription.
    subscription_id = serializers.PrimaryKeyRelatedField(
        queryset=Subscription.objects, source='subscription', write_only=True, required=False, allow_null=True
    )
    cost_center = serializers.StringRelatedField(read_only=True)
    cost_center_id = serializers.PrimaryKeyRelatedField(
        source='cost_center', queryset=CostCenter.objects,
        write_only=True, required=False, allow_null=True,
    )

    class Meta:
        model = License
        fields = (
            'id', 'name', 'software', 'software_id', 'license_type', 'license_type_display',
            'seats', 'available_seats', 'purchase_date', 'purchase_cost', 'currency',
            'order_number', 'version', 'expiration_date', 'notes', 'tags', 'tenant', 'tenant_id',
            'subscription', 'subscription_id',
            'cost_center', 'cost_center_id',
            'created_at', 'updated_at'
        )
        brief_fields = ['id', 'name', 'software', 'license_type', 'seats', 'available_seats']


class LicenseSeatAssignmentSerializer(BaseModelSerializer):
    license = LicenseSerializer(read_only=True)
    license_id = serializers.PrimaryKeyRelatedField(
        queryset=License.objects, source='license', write_only=True
    )
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects, source='asset', write_only=True, required=False, allow_null=True
    )
    assigned_holder = AssetHolderSerializer(read_only=True)
    assigned_holder_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetHolder.objects, source='assigned_holder', write_only=True, required=False, allow_null=True
    )
    # Optional precise install link (seat-level SAM).  Read: nested string repr;
    # write: bare PK via installed_software_id.
    installed_software = serializers.StringRelatedField(read_only=True)
    installed_software_id = serializers.PrimaryKeyRelatedField(
        queryset=InstalledSoftware.objects,
        source='installed_software',
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = LicenseSeatAssignment
        fields = (
            'id', 'license', 'license_id', 'asset', 'asset_id', 'assigned_holder', 'assigned_holder_id',
            'installed_software', 'installed_software_id',
            'assigned_date', 'notes', 'created_at', 'updated_at'
        )
        brief_fields = ['id', 'license', 'asset']

    def create(self, validated_data):
        # The seat-availability + locking guard lives in checkout_license(); the REST CRUD
        # path bypassed it, so N+1 POSTs could over-allocate a license and the same target
        # could consume multiple seats. Re-apply the invariant here under a row lock (held
        # through perform_create's transaction) so the API can't over-allocate.
        license_obj = validated_data['license']
        asset = validated_data.get('asset')
        holder = validated_data.get('assigned_holder')
        with transaction.atomic():
            lic = License.objects.select_for_update().get(pk=license_obj.pk)
            if lic.available_seats < 1:
                raise serializers.ValidationError(
                    {'license_id': _("No available seats left for this license.")}
                )
            existing = LicenseSeatAssignment.objects.filter(license=lic)
            if asset is not None and existing.filter(asset=asset).exists():
                raise serializers.ValidationError(
                    {'asset_id': _("This asset already holds a seat on this license.")}
                )
            if holder is not None and existing.filter(assigned_holder=holder).exists():
                raise serializers.ValidationError(
                    {'assigned_holder_id': _("This holder already holds a seat on this license.")}
                )
            validated_data['license'] = lic
            return super().create(validated_data)

