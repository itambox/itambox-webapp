from .utils import ChoiceSet

# Define choices for ObjectChange actions
class ObjectChangeActionChoices(ChoiceSet):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'

    CHOICES = (
        (ACTION_CREATE, 'Created', 'success'),
        (ACTION_UPDATE, 'Updated', 'info'),
        (ACTION_DELETE, 'Deleted', 'danger'),
    )

    LEGACY_MAP = {
        ACTION_CREATE: 1,
        ACTION_UPDATE: 2,
        ACTION_DELETE: 3,
    }

# Add other choice sets for the core app here as needed 