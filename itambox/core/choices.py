from django.utils.translation import gettext_lazy as _

from core.choice_sets import ChoiceSet


class ObjectChangeActionChoices(ChoiceSet):
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_CHECKOUT = "checkout"
    ACTION_CHECKIN = "checkin"
    ACTION_AUDIT = "audit"

    CHOICES = (
        (ACTION_CREATE, _("Created"), "success"),
        (ACTION_UPDATE, _("Updated"), "info"),
        (ACTION_DELETE, _("Deleted"), "danger"),
        (ACTION_CHECKOUT, _("Checked Out"), "warning"),
        (ACTION_CHECKIN, _("Checked In"), "primary"),
        (ACTION_AUDIT, _("Audited"), "purple"),
    )


class EventActionChoices(ChoiceSet):
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_RESTORE = "restore"
    ACTION_CHECKOUT = "checkout"
    ACTION_CHECKIN = "checkin"

    CHOICES = (
        (ACTION_CREATE, _("Create"), "success"),
        (ACTION_UPDATE, _("Update"), "info"),
        (ACTION_DELETE, _("Delete"), "danger"),
        # Emitted on a soft-delete restore (set -> None), and on the asset
        # checkout/checkin flows: the six-value V1 event vocabulary published
        # for the webhook envelope's ``event`` field.
        (ACTION_RESTORE, _("Restore"), "success"),
        (ACTION_CHECKOUT, _("Checkout"), "warning"),
        (ACTION_CHECKIN, _("Checkin"), "primary"),
    )


class JobStatusChoices(ChoiceSet):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    CHOICES = (
        (STATUS_PENDING, _("Pending"), "secondary"),
        (STATUS_RUNNING, _("Running"), "warning"),
        (STATUS_COMPLETED, _("Completed"), "success"),
        (STATUS_FAILED, _("Failed"), "danger"),
    )
