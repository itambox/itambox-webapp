from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from organization.models import TenantRole, TenantMembership

User = get_user_model()

class SCIMUserSerializer(serializers.ModelSerializer):
    schemas = serializers.SerializerMethodField(read_only=True)
    userName = serializers.CharField(source='username')
    name = serializers.SerializerMethodField(required=False)
    emails = serializers.SerializerMethodField(required=False)
    active = serializers.BooleanField(source='is_active', required=False, default=True)
    groups = serializers.SerializerMethodField(read_only=True)
    meta = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = [
            'schemas', 'id', 'userName', 'name', 'emails', 'active', 'groups', 'meta'
        ]
        read_only_fields = ['id', 'schemas', 'groups', 'meta']

    def get_schemas(self, obj):
        return ["urn:ietf:params:scim:schemas:core:2.0:User"]

    def get_name(self, obj):
        return {
            'givenName': obj.first_name or "",
            'familyName': obj.last_name or "",
            'formatted': f"{obj.first_name} {obj.last_name}".strip() or obj.username
        }

    def get_emails(self, obj):
        if obj.email:
            return [{
                'value': obj.email,
                'primary': True,
                'type': 'work'
            }]
        return []

    def get_groups(self, obj):
        tenant = self.context.get('tenant')
        if not tenant:
            return []
        # Return custom roles mapped as SCIM groups
        memberships = TenantMembership.objects.filter(user=obj, tenant=tenant).select_related('role')
        return [
            {
                'value': str(m.role.id),
                'display': m.role.name,
                '$ref': f"/api/tenants/{tenant.slug}/scim/v2/Groups/{m.role.id}"
            }
            for m in memberships
        ]

    def get_meta(self, obj):
        created_str = obj.date_joined.isoformat() if obj.date_joined else ""
        tenant_slug = self.context.get('tenant_slug', '')
        return {
            'resourceType': 'User',
            'created': created_str,
            'lastModified': created_str,
            'location': f"/api/tenants/{tenant_slug}/scim/v2/Users/{obj.id}" if tenant_slug else ""
        }


class SCIMGroupSerializer(serializers.ModelSerializer):
    schemas = serializers.SerializerMethodField(read_only=True)
    displayName = serializers.CharField(source='name')
    members = serializers.SerializerMethodField(required=False)
    meta = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = TenantRole
        fields = ['schemas', 'id', 'displayName', 'members', 'meta']
        read_only_fields = ['id', 'schemas', 'meta']

    def get_schemas(self, obj):
        return ["urn:ietf:params:scim:schemas:core:2.0:Group"]

    def get_members(self, obj):
        memberships = obj.memberships.select_related('user')
        return [
            {
                'value': str(m.user.id),
                'display': m.user.username,
                '$ref': f"/api/tenants/{obj.tenant.slug}/scim/v2/Users/{m.user.id}"
            }
            for m in memberships
        ]

    def get_meta(self, obj):
        created_str = obj.created_at.isoformat() if hasattr(obj, 'created_at') and obj.created_at else ""
        updated_str = obj.updated_at.isoformat() if hasattr(obj, 'updated_at') and obj.updated_at else created_str
        return {
            'resourceType': 'Group',
            'created': created_str,
            'lastModified': updated_str,
            'location': f"/api/tenants/{obj.tenant.slug}/scim/v2/Groups/{obj.id}"
        }


class SCIMServiceProviderConfigSerializer(serializers.Serializer):
    schemas = serializers.ListField(child=serializers.CharField())
    patch = serializers.DictField()
    bulk = serializers.DictField()
    filter = serializers.DictField()
    changePassword = serializers.DictField()
    sort = serializers.DictField()
    etag = serializers.DictField()
    authenticationSchemes = serializers.ListField(child=serializers.DictField())
