"""Concrete subscription seat-usage query owned by the subscription service."""

from django.apps import apps
from django.db.models import Q


def count_assigned_seats(subscription) -> int:
    """Count active seats whose live target belongs to the subscription tenant."""
    # Resolved through the app registry: licenses.models imports subscriptions.models.
    seat_assignment = apps.get_model("licenses", "LicenseSeatAssignment")
    # unscoped: the count is bound explicitly to the subscription and its tenant below, independent of the ambient scope.
    return (
        seat_assignment._base_manager.filter(
            license__subscription=subscription,
            license__deleted_at__isnull=True,
            deleted_at__isnull=True,
        )
        .filter(
            Q(asset__isnull=False, asset__deleted_at__isnull=True, asset__tenant_id=subscription.tenant_id)
            | Q(
                assigned_holder__isnull=False,
                assigned_holder__deleted_at__isnull=True,
                assigned_holder__tenant_id=subscription.tenant_id,
            )
        )
        .count()
    )
