"""Concrete subscription seat-usage query owned by the subscription service."""

from django.db.models import Q

from licenses.models import LicenseSeatAssignment


def count_assigned_seats(subscription) -> int:
    """Count active seats whose live target belongs to the subscription tenant."""
    return (
        LicenseSeatAssignment._base_manager.filter(
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
