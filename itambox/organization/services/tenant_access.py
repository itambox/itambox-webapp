"""Organization-owned implementation of the typed tenant access boundary."""

from __future__ import annotations

from collections.abc import Collection
from typing import cast

from core.tenant_access import MembershipRef, PrincipalRef, TenantAccessPolicy, TenantRef
from organization import access as organization_access
from organization.models import Membership


class OrganizationTenantAccessPolicy:
    def accessible_tenant_ids(self, user: PrincipalRef | None) -> set[int]:
        return organization_access.accessible_tenant_ids(user)

    def active_membership(
        self,
        user: PrincipalRef,
        tenant_id: int,
    ) -> MembershipRef | None:
        return cast(
            MembershipRef | None,
            Membership.objects.filter(
                user_id=user.pk,
                tenant_id=tenant_id,
                tenant__deleted_at__isnull=True,
                is_active=True,
            )
            .select_related("tenant")
            .first(),
        )

    def first_active_membership_in(
        self,
        user: PrincipalRef,
        authorized_tenant_ids: Collection[int],
    ) -> MembershipRef | None:
        tenant_ids = set(authorized_tenant_ids)
        if not tenant_ids:
            return None
        return cast(
            MembershipRef | None,
            Membership.objects.filter(
                user_id=user.pk,
                tenant_id__in=tenant_ids,
                tenant__deleted_at__isnull=True,
                is_active=True,
            )
            .select_related("tenant", "tenant__group")
            .first(),
        )

    def shared_stock_read_allowed(
        self,
        obj: object,
        active_tenant: TenantRef,
        user: PrincipalRef,
        perm: str | None = None,
    ) -> bool:
        return organization_access.shared_stock_read_allowed(
            obj,
            active_tenant,
            user,
            perm,
        )


organization_tenant_access_policy: TenantAccessPolicy = OrganizationTenantAccessPolicy()
