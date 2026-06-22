from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType

from itambox.api.base import BaseModelSerializer
from itambox.api.fields import ContentTypeField, validate_gfk_target_tenant
from itambox.api.serializers import GenericObjectSerializer
from organization.models import (
    Site, Region, SiteGroup, Location, Tenant, TenantGroup,
    AssetHolder, Contact, ContactRole, ContactAssignment, CostCenter,
)
from extras.api.serializers import TagSerializer


class NestedRegionSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:region-detail')

    class Meta:
        model = Region
        fields = ['id', 'url', 'name', 'slug']
        brief_fields = ['id', 'name']


class NestedSiteGroupSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:sitegroup-detail')

    class Meta:
        model = SiteGroup
        fields = ['id', 'url', 'name', 'slug']
        brief_fields = ['id', 'name']


class NestedTenantSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:tenant-detail')

    class Meta:
        model = Tenant
        fields = ['id', 'url', 'name', 'slug']
        brief_fields = ['id', 'name']


class NestedTenantGroupSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:tenantgroup-detail')

    class Meta:
        model = TenantGroup
        fields = ['id', 'url', 'name', 'slug']
        brief_fields = ['id', 'name']


class NestedSiteSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:site-detail')

    class Meta:
        model = Site
        fields = ['id', 'url', 'name', 'slug']
        brief_fields = ['id', 'name']


class NestedLocationSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:location-detail')

    class Meta:
        model = Location
        fields = ['id', 'url', 'name', 'slug']
        brief_fields = ['id', 'name']


class SiteSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:site-detail')
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
        queryset=Tenant.objects, source='tenant', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Site
        fields = [
            'id', 'url', 'name', 'slug',
            'region', 'region_id', 'group', 'group_id', 'tenant', 'tenant_id',
            'description', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'name', 'slug']


class RegionSerializer(BaseModelSerializer):
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
        brief_fields = ['id', 'url', 'name', 'slug', 'site_count']


class SiteGroupSerializer(BaseModelSerializer):
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
        brief_fields = ['id', 'url', 'name', 'slug', 'site_count']


class LocationSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:location-detail')
    site = NestedSiteSerializer(read_only=True)
    site_id = serializers.PrimaryKeyRelatedField(
        queryset=Site.objects, source='site', write_only=True
    )
    parent = NestedLocationSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects, source='parent', write_only=True, required=False, allow_null=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects, source='tenant', write_only=True, required=False, allow_null=True
    )
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Location
        fields = [
            'id', 'url', 'name', 'slug',
            'site', 'site_id', 'parent', 'parent_id', 'tenant', 'tenant_id',
            'description', 'asset_count', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'name', 'slug', 'asset_count']


class TenantGroupSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:tenantgroup-detail')
    parent = NestedTenantGroupSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=TenantGroup.objects, source='parent', write_only=True, required=False, allow_null=True
    )
    tenant_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TenantGroup
        fields = [
            'id', 'url', 'name', 'slug',
            'parent', 'parent_id', 'description', 'tenant_count',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'name', 'slug', 'tenant_count']


class TenantSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:tenant-detail')
    group = NestedTenantGroupSerializer(read_only=True)
    group_id = serializers.PrimaryKeyRelatedField(
        queryset=TenantGroup.objects, source='group', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Tenant
        fields = [
            'id', 'url', 'name', 'slug',
            'group', 'group_id', 'description', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'name', 'slug']


class AssetHolderSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:assetholder-detail')
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects, source='tenant', write_only=True, required=False, allow_null=True
    )
    assignment_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssetHolder
        fields = [
            'id', 'url', 'upn', 'email',
            'tenant', 'tenant_id', 'assignment_count',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'upn', 'email']



class NestedCostCenterSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:costcenter-detail')

    class Meta:
        model = CostCenter
        fields = ['id', 'url', 'name', 'slug', 'code']
        brief_fields = ['id', 'name', 'code']


class CostCenterSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:costcenter-detail')
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects, source='tenant', write_only=True, required=False, allow_null=True,
    )
    parent = NestedCostCenterSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=CostCenter.objects, source='parent', write_only=True, required=False, allow_null=True,
    )
    child_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = CostCenter
        fields = [
            'id', 'url', 'name', 'slug', 'code',
            'tenant', 'tenant_id', 'parent', 'parent_id',
            'description', 'is_active', 'child_count',
            'created_at', 'updated_at',
        ]
        brief_fields = ['id', 'url', 'name', 'code']


class ContactRoleSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:contactrole-detail')

    class Meta:
        model = ContactRole
        fields = ['id', 'url', 'name', 'slug', 'description', 'created_at', 'updated_at']
        brief_fields = ['id', 'url', 'name', 'slug']


class ContactSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:contact-detail')
    tags = TagSerializer(many=True, read_only=True)
    # tenant=None → global/shared contact (visible to all tenants); a set tenant
    # makes it private to that tenant. Optional on write.
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects, source='tenant', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Contact
        fields = [
            'id', 'url', 'name', 'title', 'phone', 'email',
            'web_url', 'tenant', 'tenant_id', 'description', 'comments', 'tags',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'name', 'email']


class ContactAssignmentSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:organization_api:contactassignment-detail')
    contact = ContactSerializer(read_only=True)
    contact_id = serializers.PrimaryKeyRelatedField(
        queryset=Contact.objects.all(), source='contact', write_only=True
    )
    role = ContactRoleSerializer(read_only=True)
    role_id = serializers.PrimaryKeyRelatedField(
        queryset=ContactRole.objects.all(), source='role', write_only=True
    )
    # `source='content_type'` maps this API alias to the GFK's actual model field;
    # without it, read serialization does getattr(obj, 'assigned_object_type') and
    # raises AttributeError (the list/detail endpoints 500'd). validate() already
    # accepts the value under either key.
    assigned_object_type = ContentTypeField(queryset=ContentType.objects.all(), source='content_type')
    assigned_object = GenericObjectSerializer(read_only=True)

    class Meta:
        model = ContactAssignment
        fields = [
            'id', 'url', 'contact', 'contact_id', 'role', 'role_id',
            'assigned_object_type', 'object_id', 'assigned_object',
            'priority', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'contact', 'role']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Enforce that the generic-FK target lives in the current tenant. The
        # content-type may arrive under either the serializer field name or the
        # model field name depending on `source` wiring.
        content_type = attrs.get('content_type') or attrs.get('assigned_object_type')
        object_id = attrs.get('object_id')
        if content_type is None and self.instance is not None:
            content_type = getattr(self.instance, 'content_type', None)
        if object_id is None and self.instance is not None:
            object_id = getattr(self.instance, 'object_id', None)
        validate_gfk_target_tenant(content_type, object_id)
        return attrs
