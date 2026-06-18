from django.utils.translation import gettext_lazy as _

from itambox.utils import ChoiceSet


class ObjectChangeActionChoices(ChoiceSet):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'
    ACTION_CHECKOUT = 'checkout'
    ACTION_CHECKIN = 'checkin'
    ACTION_AUDIT = 'audit'

    CHOICES = (
        (ACTION_CREATE, _('Created'), 'success'),
        (ACTION_UPDATE, _('Updated'), 'info'),
        (ACTION_DELETE, _('Deleted'), 'danger'),
        (ACTION_CHECKOUT, _('Checked Out'), 'warning'),
        (ACTION_CHECKIN, _('Checked In'), 'primary'),
        (ACTION_AUDIT, _('Audited'), 'purple'),
    )


class EventActionChoices(ChoiceSet):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'

    CHOICES = (
        (ACTION_CREATE, _('Create'), 'success'),
        (ACTION_UPDATE, _('Update'), 'info'),
        (ACTION_DELETE, _('Delete'), 'danger'),
    )


class JobStatusChoices(ChoiceSet):
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    CHOICES = (
        (STATUS_PENDING, _('Pending'), 'secondary'),
        (STATUS_RUNNING, _('Running'), 'warning'),
        (STATUS_COMPLETED, _('Completed'), 'success'),
        (STATUS_FAILED, _('Failed'), 'danger'),
    )
