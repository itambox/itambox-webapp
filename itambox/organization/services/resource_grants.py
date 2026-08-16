"""Locked lifecycle services for tenant resource grants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.choices import ObjectChangeActionChoices
from core.context import SystemAuthorizationContext, get_current_request_id
from core.models import ObjectChange
from core.tasks.context import TaskContext
from organization.access import authorize_tenant_operation
from organization.models import (
    Tenant,
    TenantResourceGrant,
    TenantResourceGrantExpiryRevocation,
)

EXPIRY_PERMISSION = "organization.delete_tenantresourcegrant"
EXPIRY_OPERATION = "organization.resource_grant.expire"
EXPIRY_REASON = "Scheduled revocation of tenant resource grants whose valid_until deadline has elapsed."
ROLLBACK_OPERATION = "organization.resource_grant.rollback"


class InvalidResourceGrantError(ValidationError):
    """A persisted grant failed a structural or allowlist invariant."""


@dataclass(frozen=True)
class RevocationResult:
    grant: TenantResourceGrant
    change: ObjectChange
    evidence: object | None


def _validate_expiry_candidate(grant: TenantResourceGrant, *, cutoff=None) -> bool:
    has_tenant = grant.grantee_tenant_id is not None
    has_group = grant.grantee_tenant_group_id is not None
    if has_tenant == has_group:
        raise InvalidResourceGrantError("The resource grant has an invalid grantee structure.")
    if grant.resource_type_id is None:
        raise InvalidResourceGrantError("The resource grant has an invalid resource type.")
    label = f"{grant.resource_type.app_label}.{grant.resource_type.model}"
    if label not in TenantResourceGrant.APPROVED_RESOURCE_MODELS:
        raise InvalidResourceGrantError("The resource grant references an unsupported resource type.")
    if cutoff is not None and (grant.valid_until is None or grant.valid_until > cutoff):
        return False
    return True


def _delete_change_for_grant(grant: TenantResourceGrant, request_id) -> list[ObjectChange]:
    grant_type = ContentType.objects.get_for_model(TenantResourceGrant)
    return list(
        ObjectChange._base_manager.filter(
            tenant_id=grant.tenant_id,
            changed_object_type=grant_type,
            changed_object_id=grant.pk,
            action=ObjectChangeActionChoices.ACTION_DELETE,
            request_id=request_id,
        )
    )


def revoke_resource_grant(  # noqa: C901
    grant_id: int,
    *,
    user: Any | None,
    active_tenant: Any | None,
    system_authorization: SystemAuthorizationContext | None = None,
    cutoff=None,
    expiry_run=None,
) -> RevocationResult | None:
    """Revoke one live grant and, for expiry, persist its exact evidence.

    The grant is always loaded through the unfiltered base manager and locked.
    A caller that lost a race receives ``None`` after the live-state recheck;
    no second model save or audit row is attempted.
    """

    request_id = get_current_request_id()
    if request_id is None:
        raise PermissionDenied("Grant revocation requires an active request context.")
    if user is None and system_authorization is None:
        raise PermissionDenied("Actorless grant revocation requires system authorization.")
    if user is not None and system_authorization is not None:
        raise PermissionDenied("Grant revocation cannot combine human and system authorization.")

    with transaction.atomic():
        grant = (
            TenantResourceGrant._base_manager.select_for_update()
            .select_related("resource_type")
            .filter(pk=grant_id)
            .first()
        )
        if grant is None:
            raise TenantResourceGrant.DoesNotExist
        if grant.deleted_at is not None:
            return None
        if not Tenant._base_manager.filter(pk=grant.tenant_id, deleted_at__isnull=True).exists():
            raise PermissionDenied("The grant owner tenant is not active.")
        if getattr(active_tenant, "pk", None) != grant.tenant_id:
            raise PermissionDenied("Grant revocation is outside the active tenant.")
        if not _validate_expiry_candidate(grant, cutoff=cutoff):
            return None

        operation = EXPIRY_OPERATION if system_authorization is not None else None
        if not authorize_tenant_operation(
            user,
            active_tenant,
            EXPIRY_PERMISSION,
            system_authorization=system_authorization,
            system_operation=operation,
        ):
            raise PermissionDenied("The caller is not authorized to revoke this grant.")
        if system_authorization is not None and system_authorization.request_id != request_id:
            raise PermissionDenied("System authorization does not match the active request.")

        triggering_valid_until = grant.valid_until
        grant.delete()
        changes = _delete_change_for_grant(grant, request_id)
        if len(changes) != 1:
            raise IntegrityError("Resource grant deletion did not produce exactly one audit change.")
        change = changes[0]

        evidence = None
        if expiry_run is not None:
            if triggering_valid_until is None or expiry_run.tenant_id != grant.tenant_id:
                raise IntegrityError("Resource grant expiry evidence failed its tenant/deadline check.")
            evidence = TenantResourceGrantExpiryRevocation.objects.create(
                run=expiry_run,
                grant=grant,
                object_change=change,
                triggering_valid_until=triggering_valid_until,
                revoked_at=grant.deleted_at,
                request_id=request_id,
            )
        return RevocationResult(grant=grant, change=change, evidence=evidence)


def restore_resource_grant(
    *,
    grant_id: int,
    tenant_id: int,
    user_id: int,
    valid_until,
) -> TenantResourceGrant:
    """Restore exactly one grant after correcting or clearing its deadline."""

    with TaskContext(tenant_id=tenant_id, user_id=user_id, operation=ROLLBACK_OPERATION) as context:
        user = context.user
        tenant = context.tenant
        if user is None or tenant is None:
            raise PermissionDenied("Rollback requires a live human tenant principal.")
        if not user.has_perm(EXPIRY_PERMISSION, obj=tenant):
            raise PermissionDenied("The operator is not authorized to restore this grant.")
        if valid_until is not None:
            if timezone.is_naive(valid_until) or valid_until <= timezone.now():
                raise ValidationError({"valid_until": "The corrected deadline must be in the future."})

        with transaction.atomic():
            grant = (
                TenantResourceGrant._base_manager.select_for_update()
                .select_related("resource_type")
                .filter(pk=grant_id)
                .first()
            )
            if grant is None or grant.tenant_id != tenant_id:
                raise TenantResourceGrant.DoesNotExist
            if grant.deleted_at is None:
                raise ValidationError("Only a revoked grant can be restored.")

            grant.snapshot()
            grant.valid_until = valid_until
            grant.deleted_at = None
            grant.full_clean()
            grant.save(update_fields=["valid_until", "deleted_at", "updated_at"])
            return grant
