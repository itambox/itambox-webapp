from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from itambox.api.base import BaseModelSerializer
from itambox.api.nested_serializers import NestedManufacturerSerializer, NestedAssetSerializer
from licenses.models import License, LicenseSeatAssignment
from licenses.services import checkout_license
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

    def validate(self, data):
        # Enforce the seat-capacity invariant on PATCH/PUT: seats cannot be reduced below
        # the number of currently-active assignments.  License.clean() holds the same rule
        # for the form/admin path; BaseModelSerializer never calls full_clean, so we call
        # assert_seat_capacity() directly here — DRY: the logic lives once on the model.
        instance = self.instance
        if instance is not None and 'seats' in data:
            try:
                instance.assert_seat_capacity(seats=data['seats'])
            except DjangoValidationError as exc:
                # Re-raise as DRF ValidationError so the response uses the expected format.
                raise serializers.ValidationError(exc.message_dict)
        return super().validate(data)


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
        # Delegate to checkout_license() so the parent-License changelog entry
        # ("Checked out seat to …") is recorded on the API path just as on the UI
        # path.  The service also holds the seat-availability lock + duplicate guard,
        # removing the previously duplicated availability logic that lived here.
        license_obj = validated_data['license']
        asset = validated_data.get('asset')
        holder = validated_data.get('assigned_holder')
        notes = validated_data.get('notes', '')
        try:
            assignment = checkout_license(
                license_obj=license_obj,
                asset=asset,
                assigned_holder=holder,
                notes=notes,
            )
        except DjangoValidationError as exc:
            # Surface service-layer errors as DRF validation errors (friendly 400 response).
            raise serializers.ValidationError(exc.messages)
        # If the caller supplied installed_software, persist it now — checkout_license
        # only wires the core assignment; the optional SAM link is layered on top.
        installed_software = validated_data.get('installed_software')
        if installed_software is not None:
            assignment.installed_software = installed_software
            assignment.save(update_fields=['installed_software'])
        return assignment

