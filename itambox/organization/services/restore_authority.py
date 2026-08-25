"""Organization-owned restore-grant authority adapter."""

from __future__ import annotations

from core.restore_authority import RestoreAuthorityValidator
from organization.services import role_grant_validation


class OrganizationRestoreAuthority:
    """Dispatch restore validation to the organization-owned guard policy."""

    def validate(self, user, obj) -> None:
        label = obj._meta.label_lower
        if label == "organization.role":
            role_grant_validation.validate_role_reactivation_grants(user, obj)
        elif label == "users.usergroup" and obj.is_active:
            role_grant_validation.validate_group_membership_grant(user, obj)


organization_restore_authority: RestoreAuthorityValidator = OrganizationRestoreAuthority()
