from django.db.models.signals import pre_save
from django.dispatch import receiver
from extras.validators import CustomValidator

@receiver(pre_save)
def pre_save_custom_validation(sender, instance, **kwargs):
    """
    Zero-intrusion signal hook that executes CustomValidator
    on the saving model instance before writing to the database.
    """
    CustomValidator.validate_object(instance)
