import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.choices import ObjectChangeActionChoices
from core.managers import get_current_tenant
from core.models import write_object_change
from core.oidc_identity import oidc_audit_excluded_fields, oidc_profile_audit_payload, oidc_sensitive_audit_enabled
from core.serialization import serialize_object
from itambox.middleware import get_current_request_id, get_current_user

logger = logging.getLogger(__name__)
User = get_user_model()


@receiver(pre_save, sender=User)
def user_pre_save(sender: object, instance: object, **kwargs: object) -> None:
    if not get_current_request_id():
        return
    if instance.pk:
        orig = User._base_manager.filter(pk=instance.pk).first()
        if orig:
            base_excluded = ["password", "last_login", "updated_at"]
            instance._prechange_snapshot = serialize_object(
                orig, exclude_fields=oidc_audit_excluded_fields(base_excluded)
            )
            if oidc_sensitive_audit_enabled():
                before = serialize_object(orig, exclude_fields=base_excluded)
                after = serialize_object(instance, exclude_fields=base_excluded)
                instance._oidc_audit_changed_fields = sorted(
                    field for field in set(before) | set(after) if before.get(field) != after.get(field)
                )


@receiver(post_save, sender=User)
def user_post_save(sender: object, instance: object, created: bool, **kwargs: object) -> None:
    request_id = get_current_request_id()
    if not request_id:
        return

    prechange_data = getattr(instance, "_prechange_snapshot", None)
    postchange_data = serialize_object(
        instance,
        exclude_fields=oidc_audit_excluded_fields(["password", "last_login", "updated_at"]),
    )

    action = ObjectChangeActionChoices.ACTION_CREATE if created else ObjectChangeActionChoices.ACTION_UPDATE

    if oidc_sensitive_audit_enabled() and not created:
        changed_fields = getattr(instance, "_oidc_audit_changed_fields", ())
        if not changed_fields:
            return
        prechange_data = oidc_profile_audit_payload(())
        postchange_data = oidc_profile_audit_payload(changed_fields)
    elif action == ObjectChangeActionChoices.ACTION_UPDATE and prechange_data == postchange_data:
        return

    # User can't inherit ChangeLoggingMixin, so emit via the shared audit-row helper
    # (single source of truth for the payload shape). User has no tenant of its own,
    # so attribute to the active request tenant.
    write_object_change(
        instance=instance,
        action=action,
        user=get_current_user(),
        request_id=request_id,
        change_tenant=get_current_tenant(),
        prechange_data=prechange_data,
        postchange_data=postchange_data,
    )


@receiver(post_delete, sender=User)
def user_post_delete(sender: object, instance: object, **kwargs: object) -> None:
    request_id = get_current_request_id()
    if not request_id:
        return

    if oidc_sensitive_audit_enabled():
        prechange_data = serialize_object(
            instance,
            exclude_fields=oidc_audit_excluded_fields(["password", "last_login", "updated_at"]),
        )
    else:
        prechange_data = getattr(instance, "_prechange_snapshot", None)
        if not prechange_data:
            prechange_data = serialize_object(
                instance,
                exclude_fields=oidc_audit_excluded_fields(["password", "last_login", "updated_at"]),
            )

    write_object_change(
        instance=instance,
        action=ObjectChangeActionChoices.ACTION_DELETE,
        user=get_current_user(),
        request_id=request_id,
        change_tenant=get_current_tenant(),
        prechange_data=prechange_data,
        postchange_data=None,
    )
