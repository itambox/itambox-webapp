from .utils import ChoiceSet


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


class EventActionChoices(ChoiceSet):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'

    CHOICES = (
        (ACTION_CREATE, 'Create', 'success'),
        (ACTION_UPDATE, 'Update', 'info'),
        (ACTION_DELETE, 'Delete', 'danger'),
    )


class JobStatusChoices(ChoiceSet):
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    CHOICES = (
        (STATUS_PENDING, 'Pending', 'secondary'),
        (STATUS_RUNNING, 'Running', 'warning'),
        (STATUS_COMPLETED, 'Completed', 'success'),
        (STATUS_FAILED, 'Failed', 'danger'),
    )
