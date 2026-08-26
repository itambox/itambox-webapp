"""Organization-owned restore-grant authority adapter."""

from __future__ import annotations

from typing import Protocol, cast

from core.restore_authority import PrincipalRef, RestoreAuthorityValidator
from organization.models import Role, Tenant
from organization.services import role_grant_validation


class _RestoreMeta(Protocol):
    label_lower: str


class _RestoreTarget(Protocol):
    _meta: _RestoreMeta
    is_active: bool


class _GuardPrincipal(Protocol):
    pk: int

    def has_perm(self, permission: str, obj: Tenant | None = None) -> bool:
        """Return whether this principal holds ``permission`` for ``obj``."""


class OrganizationRestoreAuthority:
    """Dispatch restore validation to the organization-owned guard policy."""

    def validate(self, user: PrincipalRef, obj: object) -> None:
        target = cast(_RestoreTarget, obj)
        guard_user = cast(_GuardPrincipal, user)
        label = target._meta.label_lower
        if label == "organization.role":
            role_grant_validation.validate_role_reactivation_grants(guard_user, cast(Role, target))
        elif label == "users.usergroup" and target.is_active:
            role_grant_validation.validate_group_membership_grant(guard_user, target)


organization_restore_authority: RestoreAuthorityValidator = OrganizationRestoreAuthority()
