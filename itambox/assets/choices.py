"""Centralized choice sets for the assets app (NetBox-style).

Every view, filter, form and service references these symbols instead of
string literals, so the lifecycle vocabulary has a single source of truth.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _


class StatusTypeChoices(models.TextChoices):
    """Meta-type of a StatusLabel — drives the asset lifecycle state machine."""

    PENDING = 'pending', _('Pending')
    DEPLOYABLE = 'deployable', _('Deployable')
    DEPLOYED = 'deployed', _('Deployed')
    UNDEPLOYABLE = 'undeployable', _('Undeployable')
    ARCHIVED = 'archived', _('Archived')
    IN_REPAIR = 'in_repair', _('In Repair')
    ON_ORDER = 'on_order', _('On Order')


class RequestStatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending')
    APPROVED = 'approved', _('Approved')
    PROCUREMENT = 'procurement', _('Awaiting Procurement')
    DENIED = 'denied', _('Denied')
    FULFILLED = 'fulfilled', _('Fulfilled')
    CANCELLED = 'cancelled', _('Cancelled')
