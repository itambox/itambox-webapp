from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from core.utils import log_change, serialize_object
from core.choices import ObjectChangeActionChoices
from .models import Tag

# --- Signal Handlers for Tag --- #

_tag_prechange_data = {}

@receiver(pre_save, sender=Tag)
def capture_tag_prechange(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            _tag_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist:
            _tag_prechange_data[instance.pk] = None

@receiver(post_save, sender=Tag)
def handle_tag_saved(sender, instance, created, **kwargs):
    if created:
        log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _tag_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        # Tags might have M2M changes not caught by standard model_to_dict, but this logs direct field changes.
        if prechange is not None and prechange != postchange:
            log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=Tag)
def handle_tag_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance)
    log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange) 