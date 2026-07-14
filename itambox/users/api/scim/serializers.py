from rest_framework import serializers
from django.contrib.auth import get_user_model
from organization.models import Membership
from users.models import GroupMembership, UserGroup

User = get_user_model()

class SCIMUserSerializer(serializers.ModelSerializer):
    schemas = serializers.SerializerMethodField(read_only=True)
    userName = serializers.CharField(source='username')
    name = serializers.SerializerMethodField(required=False)
    emails = serializers.SerializerMethodField(required=False)
    active = serializers.SerializerMethodField()
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

    def get_active(self, obj):
        # SCIM is tenant-scoped: report the user's active state IN THIS TENANT, i.e. the
        # membership flag (so an IdP that de-provisioned this tenant via active=false sees
        # active=false), gated by the global flag (a globally disabled user is inactive
        # everywhere). Falls back to the global flag if no tenant context is present.
        if not obj.is_active:
            return False
        tenant = self.context.get('tenant')
        if tenant is None:
            return bool(obj.is_active)
        membership = Membership.objects.filter(user=obj, tenant=tenant).first()
        return bool(membership and membership.is_active)

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
        # SCIM group discovery is ownership-scoped. Provider groups projected into
        # this tenant are authorization details, not directory groups owned by the
        # customer, and must not leak through its SCIM endpoint.
        user_groups = UserGroup.objects.filter(
            tenant=tenant,
            group_memberships__membership__user=obj,
            group_memberships__membership__tenant=tenant,
        ).distinct()
        return [
            {
                'value': str(g.id),
                'display': g.name,
                '$ref': f"/api/tenants/{tenant.slug}/scim/v2/Groups/{g.id}"
            }
            for g in user_groups
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
        model = UserGroup
        fields = ['schemas', 'id', 'displayName', 'members', 'meta']
        read_only_fields = ['id', 'schemas', 'meta']

    def get_schemas(self, obj):
        return ["urn:ietf:params:scim:schemas:core:2.0:Group"]

    def get_members(self, obj):
        # A group contains tenant Membership principals, never arbitrary global users.
        # The owner predicate is redundant for valid rows, but keeps serialization
        # fail-closed if pre-constraint data is ever imported.
        tenant_slug = self.context.get('tenant_slug', '')
        group_memberships = GroupMembership.objects.filter(
            user_group=obj,
            membership__tenant=obj.tenant,
        ).select_related('membership__user')
        return [
            {
                'value': str(group_membership.membership.user_id),
                'display': group_membership.membership.user.username,
                '$ref': (
                    f"/api/tenants/{tenant_slug}/scim/v2/Users/"
                    f"{group_membership.membership.user_id}"
                ),
            }
            for group_membership in group_memberships
        ]

    def get_meta(self, obj):
        created_str = obj.created_at.isoformat() if hasattr(obj, 'created_at') and obj.created_at else ""
        updated_str = obj.updated_at.isoformat() if hasattr(obj, 'updated_at') and obj.updated_at else created_str
        tenant_slug = self.context.get('tenant_slug', '')
        return {
            'resourceType': 'Group',
            'created': created_str,
            'lastModified': updated_str,
            'location': f"/api/tenants/{tenant_slug}/scim/v2/Groups/{obj.id}"
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
