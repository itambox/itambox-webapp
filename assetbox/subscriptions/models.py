from django.db import models
from django.urls import reverse, NoReverseMatch
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, ChangeLoggingMixin # Import the base model and the mixin
from extras.models import Tag

class Provider(ChangeLoggingMixin, BaseModel):
    """Represents the vendor/supplier of a subscription or service."""
    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Unique name of the provider (e.g., Adobe Inc.)"
    )
    account_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Account ID",
        help_text="Optional customer account number with the provider"
    )
    portal_url = models.URLField(
        blank=True,
        verbose_name="Portal URL",
        help_text="Optional URL for the provider's management portal"
    )
    admin_notes = models.TextField(
        blank=True,
        verbose_name="Admin Notes",
        help_text="Optional internal administrative notes"
    )
    support_contact = models.TextField(
        blank=True,
        help_text="Optional support contact information (email, phone, etc.)"
    )
    tags = models.ManyToManyField(
        to=Tag,
        blank=True,
        related_name='subscription_providers'
    )

    class Meta:
        ordering = ('name',)
        verbose_name = "Provider"
        verbose_name_plural = "Providers"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        try:
            return reverse('subscriptions:provider_detail', kwargs={'pk': self.pk})
        except NoReverseMatch:
            return reverse('admin:subscriptions_provider_change', args=[self.pk])


class SubscriptionTypeChoices(models.TextChoices):
    SAAS = 'saas', _('SaaS')
    SUPPORT = 'support', _('Support')
    MAINTENANCE = 'maintenance', _('Maintenance')
    LEASE = 'lease', _('Lease')
    OTHER = 'other', _('Other')


class Subscription(ChangeLoggingMixin, BaseModel):
    """Represents a recurring service agreement (SaaS, Support, etc.)."""
    name = models.CharField(
        max_length=255,
        help_text="Unique descriptive name (e.g., Adobe Creative Cloud - All Apps (Team))"
    )
    provider = models.ForeignKey(
        to=Provider,
        on_delete=models.PROTECT, # Prevent deleting provider if subscriptions exist
        related_name='subscriptions'
    )
    type = models.CharField(
        max_length=50,
        choices=SubscriptionTypeChoices.choices,
        default=SubscriptionTypeChoices.SAAS,
        verbose_name="Subscription Type"
    )
    start_date = models.DateField(
        blank=True,
        null=True,
    )
    renewal_date = models.DateField(
        blank=True,
        null=True,
        verbose_name="Next Renewal Date"
    )
    renewal_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Cost per renewal period"
    )
    term_months = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Term (Months)",
        help_text="Duration of the subscription term in months"
    )
    description = models.TextField(
        blank=True,
        help_text="Optional text detailing coverage or terms"
    )
    notes = models.TextField(
        blank=True,
        help_text="Optional internal notes"
    )
    tags = models.ManyToManyField(
        to=Tag,
        blank=True,
        related_name='subscriptions'
    )

    class Meta:
        ordering = ('provider', 'name', 'renewal_date')
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"

    def __str__(self):
        return f"{self.provider} - {self.name}"

    def get_absolute_url(self):
        try:
            return reverse('subscriptions:subscription_detail', kwargs={'pk': self.pk})
        except NoReverseMatch:
            return reverse('admin:subscriptions_subscription_change', args=[self.pk])


class SubscriptionAssignment(ChangeLoggingMixin, BaseModel):
    """Flexibly links a Subscription to the entity (or entities) it covers."""
    subscription = models.ForeignKey(
        to=Subscription,
        on_delete=models.CASCADE, # Delete assignment if subscription is deleted
        related_name='assignments'
    )
    content_type = models.ForeignKey(
        to=ContentType,
        on_delete=models.CASCADE,
        limit_choices_to={ # Optional: Limit choices for GFK
            # 'model__in': ('asset', 'softwarelicense', 'user'?), # Example limits
        }
    )
    object_id = models.PositiveBigIntegerField()
    assigned_object = GenericForeignKey(
        ct_field='content_type',
        fk_field='object_id'
    )
    assigned_date = models.DateTimeField(
        auto_now_add=True, # Set when assignment is created
        editable=False
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-assigned_date',)
        verbose_name = "Subscription Assignment"
        verbose_name_plural = "Subscription Assignments"
        # Add unique constraint to prevent assigning the same subscription to the same object twice?
        # unique_together = ('subscription', 'content_type', 'object_id')

    def __str__(self):
        if self.assigned_object:
            return f"Subscription {self.subscription} assigned to {self.assigned_object}"
        return f"Subscription {self.subscription} assignment (object missing)"

    def get_absolute_url(self):
        if self.subscription:
            return self.subscription.get_absolute_url()
        try:
            return reverse('admin:subscriptions_subscriptionassignment_changelist')
        except NoReverseMatch:
            return "#"
