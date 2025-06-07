from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

# Don't import models at module level here
# from .models import ObjectChange, ObjectChangeActionChoices 
from .utils import serialize_object, get_change_context

# List of models to ignore for change logging
IGNORE_MODELS = {
    'admin.logentry',
    'contenttypes.contenttype',
    'sessions.session',
    'extras.objectchange',
}

@receiver(post_save)
def handle_changed_object(sender, instance, created, **kwargs):
    # Import models inside the handler using absolute path
    from extras.models import ObjectChange, ObjectChangeActionChoices
    
    # Check if model should be ignored
    app_label = sender._meta.app_label
    model_name = sender._meta.model_name
    if f'{app_label}.{model_name}' in IGNORE_MODELS:
        return

    # Get request context
    context = get_change_context()
    user = context.get('user')
    request_id = context.get('request_id')

    # Determine action
    action = ObjectChangeActionChoices.ACTION_CREATE if created else ObjectChangeActionChoices.ACTION_UPDATE

    # Serialize the object state
    object_data = serialize_object(instance)
    object_repr = str(instance)[:200]

    # Create the ObjectChange record
    ObjectChange.objects.create(
        user=user,
        request_id=request_id,
        action=action,
        changed_object_type=ContentType.objects.get_for_model(sender),
        changed_object_id=instance.pk,
        object_repr=object_repr,
        object_data=object_data,
    )

@receiver(post_delete)
def handle_deleted_object(sender, instance, **kwargs):
    # Import models inside the handler using absolute path
    from extras.models import ObjectChange, ObjectChangeActionChoices
    
    # Check if model should be ignored
    app_label = sender._meta.app_label
    model_name = sender._meta.model_name
    if f'{app_label}.{model_name}' in IGNORE_MODELS:
        return

    # Get request context
    context = get_change_context()
    user = context.get('user')
    request_id = context.get('request_id')

    # Serialize the object state just before deletion
    object_data = serialize_object(instance)
    object_repr = str(instance)[:200]

    # Create the ObjectChange record
    ObjectChange.objects.create(
        user=user,
        request_id=request_id,
        action=ObjectChangeActionChoices.ACTION_DELETE,
        changed_object_type=ContentType.objects.get_for_model(sender),
        changed_object_id=instance.pk,
        object_repr=object_repr,
        object_data=object_data,
    ) 