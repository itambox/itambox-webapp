import logging
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from core.models import write_object_change
from itambox.utils import serialize_object
from itambox.middleware import get_current_user, get_current_request_id
from core.managers import get_current_tenant
from core.choices import ObjectChangeActionChoices

logger = logging.getLogger(__name__)
User = get_user_model()

@receiver(pre_save, sender=User)
def user_pre_save(sender, instance, **kwargs):
    if not get_current_request_id():
        return
    if instance.pk:
        orig = User._base_manager.filter(pk=instance.pk).first()
        if orig:
            instance._prechange_snapshot = serialize_object(
                orig, 
                exclude_fields=['password', 'last_login', 'updated_at']
            )

@receiver(post_save, sender=User)
def user_post_save(sender, instance, created, **kwargs):
    request_id = get_current_request_id()
    if not request_id:
        return

    prechange_data = getattr(instance, '_prechange_snapshot', None)
    postchange_data = serialize_object(
        instance, 
        exclude_fields=['password', 'last_login', 'updated_at']
    )
    
    action = ObjectChangeActionChoices.ACTION_CREATE if created else ObjectChangeActionChoices.ACTION_UPDATE
    
    if action == ObjectChangeActionChoices.ACTION_UPDATE and prechange_data == postchange_data:
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
def user_post_delete(sender, instance, **kwargs):
    request_id = get_current_request_id()
    if not request_id:
        return

    prechange_data = getattr(instance, '_prechange_snapshot', None)
    if not prechange_data:
        prechange_data = serialize_object(
            instance,
            exclude_fields=['password', 'last_login', 'updated_at']
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
