from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from rest_framework import serializers

from organization.models import Membership
from users.models import GroupMembership, UserGroup

_UNSET = object()

User = get_user_model()


def _scim_base_path(context):
    tenant_slug = context.get("tenant_slug", "")
    return context.get("scim_base_path") or (f"/api/tenants/{tenant_slug}/scim/v2" if tenant_slug else "")


class SCIMUserSerializer(serializers.ModelSerializer):
    schemas = serializers.SerializerMethodField(read_only=True)
    id = serializers.UUIDField(source="scim_id", read_only=True)
    userName = serializers.CharField(source="username")
    externalId = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField(required=False)
    emails = serializers.SerializerMethodField(required=False)
    active = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField(read_only=True)
    meta = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = ["schemas", "id", "externalId", "userName", "name", "emails", "active", "groups", "meta"]
        read_only_fields = ["id", "externalId", "schemas", "groups", "meta"]

    def get_schemas(self, obj):
        return ["urn:ietf:params:scim:schemas:core:2.0:User"]

    def _get_scim_membership(self, obj):
        cached = getattr(obj, "_scim_membership", _UNSET)
        if cached is not _UNSET:
            return cached
        prefetched = getattr(obj, "_scim_memberships", None)
        if prefetched is not None:
            membership = prefetched[0] if prefetched else None
        else:
            tenant = self.context.get("tenant")
            membership = Membership.objects.filter(user=obj, tenant=tenant).first() if tenant is not None else None
        obj._scim_membership = membership
        return membership

    def get_externalId(self, obj):
        membership = self._get_scim_membership(obj)
        return membership.external_id if membership and membership.external_id else None

    def get_active(self, obj):
        # SCIM is tenant-scoped: report the user's active state IN THIS TENANT, i.e. the
        # membership flag (so an IdP that de-provisioned this tenant via active=false sees
        # active=false), gated by the global flag (a globally disabled user is inactive
        # everywhere). Falls back to the global flag if no tenant context is present.
        if not obj.is_active:
            return False
        tenant = self.context.get("tenant")
        if tenant is None:
            return bool(obj.is_active)
        membership = self._get_scim_membership(obj)
        return bool(membership and membership.is_active)

    def get_name(self, obj):
        return {
            "givenName": obj.first_name or "",
            "familyName": obj.last_name or "",
            "formatted": f"{obj.first_name} {obj.last_name}".strip() or obj.username,
        }

    def get_emails(self, obj):
        if obj.email:
            return [{"value": obj.email, "primary": True, "type": "work"}]
        return []

    def get_groups(self, obj):
        tenant = self.context.get("tenant")
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
        base_path = _scim_base_path(self.context)
        return [
            {
                "value": str(g.scim_id),
                "display": g.name,
                **({"$ref": f"{base_path}/Groups/{g.scim_id}"} if base_path else {}),
            }
            for g in user_groups
        ]

    def get_meta(self, obj):
        created_str = obj.date_joined.isoformat() if obj.date_joined else ""
        last_modified_str = self._get_last_modified(obj) or created_str
        base_path = _scim_base_path(self.context)
        return {
            "resourceType": "User",
            "created": created_str,
            "lastModified": last_modified_str,
            "location": f"{base_path}/Users/{obj.scim_id}" if base_path else "",
        }

    def _get_last_modified(self, obj):
        # inline import: app-registry: avoid AppRegistryNotReady at module-load time
        from core.models import ObjectChange

        user_ct = ContentType.objects.get_for_model(obj)
        change_filter = Q(changed_object_type=user_ct, changed_object_id=obj.pk)
        tenant = self.context.get("tenant")
        if tenant is not None:
            membership = self._get_scim_membership(obj)
            if membership is not None:
                membership_ct = ContentType.objects.get_for_model(Membership)
                change_filter |= Q(changed_object_type=membership_ct, changed_object_id=membership.pk)
        last_change = (
            ObjectChange.objects.filter(change_filter).order_by("-time").values_list("time", flat=True).first()
        )
        return last_change.isoformat() if last_change else None


class SCIMGroupSerializer(serializers.ModelSerializer):
    schemas = serializers.SerializerMethodField(read_only=True)
    id = serializers.UUIDField(source="scim_id", read_only=True)
    displayName = serializers.CharField(source="name")
    externalId = serializers.CharField(source="external_id", read_only=True)
    members = serializers.SerializerMethodField(required=False)
    meta = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = UserGroup
        fields = ["schemas", "id", "externalId", "displayName", "members", "meta"]
        read_only_fields = ["id", "externalId", "schemas", "meta"]

    def get_schemas(self, obj):
        return ["urn:ietf:params:scim:schemas:core:2.0:Group"]

    def get_members(self, obj):
        # A group contains tenant Membership principals, never arbitrary global users.
        # The owner predicate is redundant for valid rows, but keeps serialization
        # fail-closed if pre-constraint data is ever imported.
        base_path = _scim_base_path(self.context)
        group_memberships = GroupMembership.objects.filter(
            user_group=obj,
            membership__tenant=obj.tenant,
        ).select_related("membership__user")
        return [
            {
                "value": str(group_membership.membership.user.scim_id),
                "display": group_membership.membership.user.username,
                **({"$ref": f"{base_path}/Users/{group_membership.membership.user.scim_id}"} if base_path else {}),
            }
            for group_membership in group_memberships
        ]

    def get_meta(self, obj):
        created_str = obj.created_at.isoformat() if hasattr(obj, "created_at") and obj.created_at else ""
        updated_str = obj.updated_at.isoformat() if hasattr(obj, "updated_at") and obj.updated_at else created_str
        base_path = _scim_base_path(self.context)
        return {
            "resourceType": "Group",
            "created": created_str,
            "lastModified": updated_str,
            "location": f"{base_path}/Groups/{obj.scim_id}" if base_path else "",
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
