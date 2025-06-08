# organization/api/serializers.py
from rest_framework import serializers
# Use app-relative imports for models within the same Django project
from organization.models import Site, Region, SiteGroup, Location, Tenant, TenantGroup, AssetHolder, AssetHolderAssignment
from django.contrib.contenttypes.models import ContentType
from assetbox.api.fields import ContentTypeField
from assetbox.api.serializers import GenericObjectSerializer

# Inspired by NetBox API serializers

#
# Nested Serializers (for read-only representations)
#

class NestedRegionSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:region-detail')

    class Meta:
        model = Region
        fields = ['id', 'url', 'name', 'slug']

class NestedSiteGroupSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:sitegroup-detail')

    class Meta:
        model = SiteGroup
        fields = ['id', 'url', 'name', 'slug']

class NestedTenantSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:tenant-detail')

    class Meta:
        model = Tenant
        fields = ['id', 'url', 'name', 'slug']

class NestedTenantGroupSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:tenantgroup-detail')

    class Meta:
        model = TenantGroup
        fields = ['id', 'url', 'name', 'slug']

class NestedSiteSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:site-detail')

    class Meta:
        model = Site
        fields = ['id', 'url', 'name', 'slug']

class NestedLocationSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:location-detail')

    class Meta:
        model = Location
        fields = ['id', 'url', 'name', 'slug']

#
# Main Serializers (for list/detail views)
#

class SiteSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:site-detail')
    # Use nested serializers for read, allow writing via PK
    region = NestedRegionSerializer(read_only=True)
    region_id = serializers.PrimaryKeyRelatedField(
        queryset=Region.objects.all(), source='region', write_only=True, required=False, allow_null=True
    )
    group = NestedSiteGroupSerializer(read_only=True)
    group_id = serializers.PrimaryKeyRelatedField(
        queryset=SiteGroup.objects.all(), source='group', write_only=True, required=False, allow_null=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(), source='tenant', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Site
        # Include fields relevant for API representation, add nested and writeable PK fields
        fields = [
            'id', 'url', 'name', 'slug',
            'region', 'region_id', 'group', 'group_id', 'tenant', 'tenant_id',
            'description', 'created_at', 'updated_at'
        ]

class RegionSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:region-detail')
    parent = NestedRegionSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=Region.objects.all(), source='parent', write_only=True, required=False, allow_null=True
    )
    site_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Region
        fields = [
            'id', 'url', 'name', 'slug',
            'parent', 'parent_id', 'description', 'site_count',
            'created_at', 'updated_at'
        ]

class SiteGroupSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:sitegroup-detail')
    parent = NestedSiteGroupSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=SiteGroup.objects.all(), source='parent', write_only=True, required=False, allow_null=True
    )
    site_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = SiteGroup
        fields = [
            'id', 'url', 'name', 'slug',
            'parent', 'parent_id', 'description', 'site_count',
            'created_at', 'updated_at'
        ]

class LocationSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:location-detail')
    site = NestedSiteSerializer(read_only=True)
    site_id = serializers.PrimaryKeyRelatedField(
        queryset=Site.objects.all(), source='site', write_only=True # Site is required for Location
    )
    parent = NestedLocationSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), source='parent', write_only=True, required=False, allow_null=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(), source='tenant', write_only=True, required=False, allow_null=True
    )
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Location
        fields = [
            'id', 'url', 'name', 'slug',
            'site', 'site_id', 'parent', 'parent_id', 'tenant', 'tenant_id',
            'description', 'asset_count', 'created_at', 'updated_at'
        ]

class TenantGroupSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:tenantgroup-detail')
    parent = NestedTenantGroupSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=TenantGroup.objects.all(), source='parent', write_only=True, required=False, allow_null=True
    )
    tenant_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TenantGroup
        fields = [
            'id', 'url', 'name', 'slug',
            'parent', 'parent_id', 'description', 'tenant_count',
            'created_at', 'updated_at'
        ]

class TenantSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:tenant-detail')
    group = NestedTenantGroupSerializer(read_only=True)
    group_id = serializers.PrimaryKeyRelatedField(
        queryset=TenantGroup.objects.all(), source='group', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Tenant
        fields = [
            'id', 'url', 'name', 'slug',
            'group', 'group_id', 'description', 'created_at', 'updated_at'
        ]

class AssetHolderSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:assetholder-detail')
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(), source='tenant', write_only=True, required=False, allow_null=True
    )
    assignment_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssetHolder
        fields = [
            'id', 'url', 'upn', 'email',
            'tenant', 'tenant_id', 'assignment_count',
            'created_at', 'updated_at'
        ]

class AssetHolderAssignmentSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:assetholderassignment-detail')
    asset_holder = AssetHolderSerializer(read_only=True)
    assigned_object_type = ContentTypeField(
        queryset=ContentType.objects.all()
    )
    assigned_object = GenericObjectSerializer(read_only=True)

    class Meta:
        model = AssetHolderAssignment
        fields = [
            'id', 'url', 'asset_holder', 'assigned_object_type', 'assigned_object_id',
            'assigned_object', 'created_at', 'updated_at'
        ]
        read_only_fields = fields

# Add serializers for SiteGroup, Location, Tenant, TenantGroup, AssetHolder below... 