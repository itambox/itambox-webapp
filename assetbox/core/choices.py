from .utils import ChoiceSet

# Define choices for ObjectChange actions
class ObjectChangeActionChoices(ChoiceSet):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'
    ACTION_CHECKOUT = 'checkout'
    ACTION_CHECKIN = 'checkin'
    ACTION_AUDIT = 'audit'

    CHOICES = (
        (ACTION_CREATE, 'Created', 'success'),
        (ACTION_UPDATE, 'Updated', 'info'),
        (ACTION_DELETE, 'Deleted', 'danger'),
        (ACTION_CHECKOUT, 'Checked Out', 'warning'),
        (ACTION_CHECKIN, 'Checked In', 'primary'),
        (ACTION_AUDIT, 'Audited', 'purple'),
    )

    LEGACY_MAP = {
        ACTION_CREATE: 1,
        ACTION_UPDATE: 2,
        ACTION_DELETE: 3,
    }

# Add other choice sets for the core app here as needed 