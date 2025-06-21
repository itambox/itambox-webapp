from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from assets.models import ConsumableAssignment, Consumable
from core.models import Notification

User = get_user_model()

@receiver(post_save, sender=ConsumableAssignment)
@receiver(post_delete, sender=ConsumableAssignment)
def check_consumable_stock(sender, instance, **kwargs):
    consumable = instance.consumable
    remaining = consumable.remaining_qty
    min_qty = consumable.min_qty
    
    # Check if stock dips below safety threshold
    if remaining < min_qty:
        # Determine warning level and message
        if remaining <= 0:
            level = Notification.LEVEL_DANGER
            subject = f"Out of Stock: {consumable.name}"
            message = f"The consumable item '{consumable}' is completely OUT OF STOCK! Remaining count is {remaining} (Safety threshold: {min_qty})."
        else:
            level = Notification.LEVEL_WARNING
            subject = f"Low Stock Warning: {consumable.name}"
            message = f"The consumable item '{consumable}' is running low. Remaining count is {remaining} (Safety threshold: {min_qty})."
        
        # Dispatch notification to all administrators and staff members
        admins = User.objects.filter(is_staff=True)
        for admin in admins:
            # Prevent creating duplicate unread warnings for the same consumable
            if not Notification.objects.filter(user=admin, subject=subject, is_read=False).exists():
                Notification.objects.create(
                    user=admin,
                    subject=subject,
                    message=message,
                    level=level
                )
