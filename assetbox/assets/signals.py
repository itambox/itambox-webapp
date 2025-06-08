from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from core.utils import log_change, serialize_object
from core.choices import ObjectChangeActionChoices
from .models import Asset, Manufacturer, AssetRole

# Store previous instance state for updates
_prechange_data = {}
_manufacturer_prechange_data = {}
_assetrole_prechange_data = {}

@receiver(pre_save, sender=Asset)
def capture_asset_prechange(sender, instance, **kwargs):
    """
    Capture the state of an Asset *before* it's saved (for updates).
    """
    if instance.pk:
        # Instance exists, so this is an update. Fetch original state.
        try:
            old_instance = Asset.objects.get(pk=instance.pk)
            _prechange_data[instance.pk] = serialize_object(old_instance)
            print(f"[SIGNAL] Captured prechange data for Asset {instance.pk}") # DEBUG
        except Asset.DoesNotExist:
            # Should not happen in pre_save for an existing PK, but handle defensively
            _prechange_data[instance.pk] = None
            print(f"[SIGNAL] Warning: Could not find old instance for Asset {instance.pk} in pre_save") # DEBUG

@receiver(post_save, sender=Asset)
def handle_asset_saved(sender, instance, created, **kwargs):
    """
    Log the creation or update of an Asset.
    """
    if created:
        action = ObjectChangeActionChoices.ACTION_CREATE
        postchange_data = serialize_object(instance)
        log_change(instance, action, postchange_data=postchange_data)
    else:
        # Update: Retrieve prechange data captured by pre_save signal
        prechange = _prechange_data.pop(instance.pk, None)
        postchange_data = serialize_object(instance)

        # Check if data actually changed (requires both pre/post data)
        if prechange is not None and prechange != postchange_data:
            print(f"[SIGNAL] Logging update for Asset {instance.pk}") # DEBUG
            action = ObjectChangeActionChoices.ACTION_UPDATE
            log_change(instance, action, prechange_data=prechange, postchange_data=postchange_data)
        elif prechange is None:
            print(f"[SIGNAL] Warning: Skipping update log for Asset {instance.pk} - prechange data not found.") # DEBUG
        # else: data didn't change, no need to log

@receiver(post_delete, sender=Asset)
def handle_asset_deleted(sender, instance, **kwargs):
    print(f"[SIGNAL] handle_asset_deleted called for {instance}") # DEBUG
    action = ObjectChangeActionChoices.ACTION_DELETE
    try:
        prechange_data = serialize_object(instance)
        print(f"[SIGNAL] Serialized prechange_data: {prechange_data}") # DEBUG
    except Exception as e:
        print(f"[SIGNAL] Error serializing object in handle_asset_deleted: {e}") # DEBUG
        prechange_data = None # Avoid calling log_change with potentially bad data
        return # Stop if serialization fails

    # Only call log_change if serialization seemed successful
    if prechange_data is not None:
        log_change(instance, action, prechange_data=prechange_data)
    else:
        print("[SIGNAL] Skipping log_change due to serialization error or None data.") # DEBUG

# --- Signal Handlers for Manufacturer --- #

@receiver(pre_save, sender=Manufacturer)
def capture_manufacturer_prechange(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            _manufacturer_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist:
            _manufacturer_prechange_data[instance.pk] = None

@receiver(post_save, sender=Manufacturer)
def handle_manufacturer_saved(sender, instance, created, **kwargs):
    if created:
        log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _manufacturer_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange:
            log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=Manufacturer)
def handle_manufacturer_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance)
    log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange)

# --- Signal Handlers for AssetRole --- #

@receiver(pre_save, sender=AssetRole)
def capture_assetrole_prechange(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            _assetrole_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist:
            _assetrole_prechange_data[instance.pk] = None

@receiver(post_save, sender=AssetRole)
def handle_assetrole_saved(sender, instance, created, **kwargs):
    if created:
        log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _assetrole_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange:
            log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=AssetRole)
def handle_assetrole_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance)
    log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange) 