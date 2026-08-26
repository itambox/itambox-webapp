"""Atomic mutation services for provider SCIM PATCH operations."""

import logging
from collections.abc import Collection

from django.db import IntegrityError, transaction
from django.db.models import Q

from organization.models import Membership
from organization.services.role_grant_validation import validate_group_membership_grant
from users.api.scim.identifiers import identifier_lookup_or_none
from users.api.scim.provider_patch import UNSET, GroupPatch, SCIMPatchError, UserPatch
from users.models import GroupMembership, User, UserGroup

logger = logging.getLogger("itambox.scim.provider_services")


def _require_provider_actor(tenant, actor, *, permission: str) -> None:
    if not getattr(tenant, "is_provider", False):
        raise SCIMPatchError("SCIM mutation requires a provider tenant", status_code=403)
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise SCIMPatchError("SCIM mutation requires an authenticated actor", status_code=401)
    if not getattr(actor, "is_superuser", False) and not actor.has_perm(permission, obj=tenant):
        raise SCIMPatchError(f"SCIM mutation requires permission: {permission}", status_code=403)


def _resolve_provider_member_ids(tenant, identifiers):
    legacy_ids = set()
    opaque_ids = set()
    invalid_count = 0
    for identifier in set(identifiers):
        lookup = identifier_lookup_or_none(identifier)
        if lookup is None:
            invalid_count += 1
        elif "pk" in lookup:
            legacy_ids.add(lookup["pk"])
        else:
            opaque_ids.add(lookup["scim_id"])

    identifier_filter = Q()
    if legacy_ids:
        identifier_filter |= Q(pk__in=legacy_ids)
    if opaque_ids:
        identifier_filter |= Q(scim_id__in=opaque_ids)
    if not identifier_filter:
        return set(), invalid_count

    resolved_rows = list(
        User.objects.filter(
            memberships__tenant=tenant,
            memberships__is_active=True,
        )
        .filter(identifier_filter)
        .values_list("pk", "scim_id")
        .distinct()
    )
    resolved_user_ids = {user_pk for user_pk, _ in resolved_rows}
    matched_identifiers = sum(
        (user_pk in legacy_ids) + (opaque_id in opaque_ids) for user_pk, opaque_id in resolved_rows
    )
    skipped_count = invalid_count + len(legacy_ids) + len(opaque_ids) - matched_identifiers
    return resolved_user_ids, skipped_count


def _log_skipped_provider_members(tenant, skipped_count):
    if skipped_count:
        logger.warning(
            "SCIM provider group sync skipped %s requested user identifiers: not active staff of provider %s "
            "(provision via SCIM /Users first).",
            skipped_count,
            tenant.slug,
        )


def _sync_provider_group_members(tenant, group, member_ids, *, actor):
    """Reconcile provider-owned SCIM memberships without touching other sources.

    Only active staff of this provider tenant are eligible. SCIM rows are the only rows
    reconciled; manually managed or other-source memberships remain untouched. Adding
    a role-bearing group member runs the canonical escalation guard.
    """
    _require_provider_actor(tenant, actor, permission="organization.change_membership")
    if group.tenant_id != tenant.pk:
        raise SCIMPatchError(
            "Provider SCIM may modify only groups owned by its provider tenant.",
            status_code=403,
        )
    group = UserGroup.objects.select_for_update().get(pk=group.pk, tenant=tenant)
    requested_user_ids, skipped_count = _resolve_provider_member_ids(tenant, member_ids)
    memberships_by_user_id = {
        membership.user_id: membership
        for membership in Membership.objects.filter(
            user_id__in=requested_user_ids,
            tenant=tenant,
            is_active=True,
        ).select_related("user")
    }
    _log_skipped_provider_members(
        tenant,
        skipped_count + len(requested_user_ids - set(memberships_by_user_id)),
    )

    # Apply only the delta (add/remove) so ChangeLoggingMixin does not fire on unchanged
    # members.
    current_rows = {row.membership.user_id: row for row in group.group_memberships.select_related("membership__user")}
    membership_changed = False
    additions = set(memberships_by_user_id) - set(current_rows)
    if additions:
        validate_group_membership_grant(actor, group)

    for user_id, membership in memberships_by_user_id.items():
        row = current_rows.get(user_id)
        if row is None:
            GroupMembership.objects.create(
                user_group=group,
                membership=membership,
                source=GroupMembership.SOURCE_SCIM,
                external_id=str(membership.user.scim_id),
                added_by=actor,
            )
            membership_changed = True
            continue
        if row.source == GroupMembership.SOURCE_SCIM and row.external_id != str(row.membership.user.scim_id):
            row.external_id = str(row.membership.user.scim_id)
            row.save(update_fields=["external_id"])
            membership_changed = True

    desired_user_ids = set(memberships_by_user_id)
    for user_id, row in current_rows.items():
        if row.source == GroupMembership.SOURCE_SCIM and user_id not in desired_user_ids:
            row.delete()
            membership_changed = True
    if membership_changed:
        group.save(update_fields=["updated_at"])


@transaction.atomic
def sync_provider_group_members(
    tenant: object,
    group: UserGroup,
    member_ids: Collection[int | str],
    *,
    actor: object,
) -> None:
    """Update an existing provider group with SCIM-owned memberships."""
    _require_provider_actor(tenant, actor, permission="users.change_usergroup")
    return _sync_provider_group_members(tenant, group, member_ids, actor=actor)


def _apply_provider_user_active_state(user, tenant, active) -> None:
    if active is UNSET:
        return
    membership = Membership.objects.filter(user=user, tenant=tenant).first()
    if membership is not None and membership.is_active != active:
        membership.is_active = active
        membership.save(update_fields=["is_active"])

    # Mirror the global flag to "has any active membership anywhere": clear login only
    # when the user is fully de-provisioned, never from one tenant while another remains.
    any_active = Membership.objects.filter(user=user, is_active=True).exists()
    if user.is_active != any_active:
        user.is_active = any_active
        user.save(update_fields=["is_active"])


def _save_provider_user_external_id(user, tenant, external_id) -> None:
    if external_id is UNSET:
        return
    membership = Membership.objects.filter(user=user, tenant=tenant).first()
    if membership is None or membership.external_id == external_id:
        return
    membership.external_id = external_id
    try:
        with transaction.atomic():
            membership.save(update_fields=["external_id"])
    except IntegrityError as exc:
        raise SCIMPatchError(
            "externalId is already used in this provider tenant",
            scim_type="uniqueness",
            status_code=409,
        ) from exc


def _save_provider_user_identity(user, patch: UserPatch) -> None:
    identity_fields = []
    for field_name in ("username", "email", "first_name", "last_name"):
        value = getattr(patch, field_name)
        if value is not UNSET:
            setattr(user, field_name, value)
            identity_fields.append(field_name)
    if not identity_fields:
        return
    try:
        user.save(update_fields=identity_fields)
    except IntegrityError as exc:
        raise SCIMPatchError(
            "User identity conflicts with an existing user",
            scim_type="uniqueness",
            status_code=409,
        ) from exc


def apply_provider_user_patch(user: User, tenant: object, patch: UserPatch, *, actor: object) -> User:
    """Apply a parsed provider-user update in one transaction."""
    _require_provider_actor(tenant, actor, permission="organization.change_membership")
    with transaction.atomic():
        try:
            user = type(user)._base_manager.select_for_update().get(pk=user.pk)
        except type(user).DoesNotExist as exc:
            raise SCIMPatchError("SCIM user was deleted", status_code=404) from exc
        if not Membership.objects.filter(user=user, tenant=tenant).exists():
            raise SCIMPatchError("SCIM user is not staff of this provider", status_code=404)
        # A membership row still shares the global identity after deprovisioning. Do
        # not let one provider rewrite identity fields while any other tenant retains
        # a historical or inactive membership for the same user.
        has_other = Membership.objects.filter(user=user).exclude(tenant=tenant).exists()
        _apply_provider_user_active_state(user, tenant, patch.active)
        _save_provider_user_external_id(user, tenant, patch.external_id)
        if has_other:
            # Leave the shared global identity alone and make the skipped mutation visible
            # to operators without changing the established SCIM 200 response contract.
            logger.warning(
                "SCIM provider identity update skipped for shared user %s by actor %s in tenant %s",
                user.pk,
                getattr(actor, "pk", "?"),
                tenant.slug,
            )
            return user
        _save_provider_user_identity(user, patch)
        return user


def ensure_provider_group_external_id_available(
    tenant: object,
    external_id: str,
    *,
    exclude_pk: int | None = None,
) -> None:
    if not external_id:
        return
    queryset = UserGroup.objects.filter(tenant=tenant, external_id=external_id)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    if queryset.exists():
        raise SCIMPatchError("Group externalId already exists", scim_type="uniqueness", status_code=409)


def ensure_provider_group_name_available(
    tenant: object,
    name: str,
    *,
    exclude_pk: int | None = None,
) -> None:
    queryset = UserGroup.objects.filter(tenant=tenant, name=name)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    if queryset.exists():
        raise SCIMPatchError("Group already exists", scim_type="uniqueness", status_code=409)


@transaction.atomic
def create_provider_group(
    tenant: object,
    name: str,
    member_ids: Collection[int | str],
    *,
    actor: object,
    external_id: str = "",
) -> UserGroup:
    _require_provider_actor(tenant, actor, permission="users.add_usergroup")
    ensure_provider_group_external_id_available(tenant, external_id)
    try:
        group = UserGroup.objects.create(tenant=tenant, name=name, external_id=external_id)
    except IntegrityError as exc:
        raise SCIMPatchError("Group already exists", scim_type="uniqueness", status_code=409) from exc
    _sync_provider_group_members(tenant, group, member_ids, actor=actor)
    return group


def save_provider_group(group: UserGroup, tenant: object, *, actor: object) -> None:
    _require_provider_actor(tenant, actor, permission="users.change_usergroup")
    _save_group_or_raise(group, translate_integrity=True)


def _set_group_external_id(tenant, group, external_id) -> None:
    if external_id is UNSET:
        return
    ensure_provider_group_external_id_available(tenant, external_id, exclude_pk=group.pk)
    group.external_id = external_id


def _set_group_display_name(tenant, group, display_name) -> None:
    if display_name is UNSET:
        return
    ensure_provider_group_name_available(tenant, display_name, exclude_pk=group.pk)
    group.name = display_name


def _resolved_operation_member_ids(tenant, operation):
    resolved_ids, skipped_count = _resolve_provider_member_ids(tenant, operation.member_ids)
    _log_skipped_provider_members(tenant, skipped_count)
    return resolved_ids


def _apply_group_member_operation(tenant, current_member_ids, operation):
    resolved_member_ids = _resolved_operation_member_ids(tenant, operation)
    if operation.op == "add":
        current_member_ids.update(resolved_member_ids)
    elif operation.op == "remove":
        if operation.filter_member_id is not None:
            resolved_filter_ids, skipped_count = _resolve_provider_member_ids(tenant, (operation.filter_member_id,))
            _log_skipped_provider_members(tenant, skipped_count)
            current_member_ids.difference_update(resolved_filter_ids)
        elif resolved_member_ids:
            current_member_ids.difference_update(resolved_member_ids)
        elif operation.clear_members:
            current_member_ids.clear()
    elif operation.op == "replace":
        return resolved_member_ids
    return current_member_ids


def _apply_group_member_operations(tenant, group, operations):
    current_member_ids = set(group.group_memberships.values_list("membership__user_id", flat=True))
    for operation in operations:
        current_member_ids = _apply_group_member_operation(tenant, current_member_ids, operation)
    return current_member_ids


def _save_group_or_raise(group, *, translate_integrity: bool) -> None:
    try:
        group.save()
    except IntegrityError as exc:
        if not translate_integrity:
            raise
        raise SCIMPatchError(
            "Group already exists",
            scim_type="uniqueness",
            status_code=409,
        ) from exc


def apply_provider_group_patch(
    tenant: object,
    group: UserGroup,
    patch: GroupPatch,
    *,
    actor: object,
) -> UserGroup:
    """Apply a parsed provider-group update and reconcile memberships atomically."""
    _require_provider_actor(tenant, actor, permission="users.change_usergroup")
    with transaction.atomic():
        try:
            group = UserGroup.objects.select_for_update().get(pk=group.pk, tenant=tenant)
        except UserGroup.DoesNotExist as exc:
            raise SCIMPatchError("SCIM group was deleted", status_code=404) from exc
        _set_group_external_id(tenant, group, patch.external_id)
        _set_group_display_name(tenant, group, patch.display_name)
        if patch.member_operations:
            current_member_ids = _apply_group_member_operations(tenant, group, patch.member_operations)
        else:
            current_member_ids = None

        # Persist the mutation inside the same transaction as membership reconciliation.
        _save_group_or_raise(
            group,
            translate_integrity=patch.display_name is not UNSET or patch.external_id is not UNSET,
        )
        if current_member_ids is not None:
            sync_provider_group_members(
                tenant,
                group,
                current_member_ids,
                actor=actor,
            )
        return group
