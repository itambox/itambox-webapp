from django.db import models
from django.conf import settings
from django.urls import reverse, NoReverseMatch
from core.models import BaseModel, ChangeLoggingMixin
from core.mixins import TaggableMixin, JournalingMixin, ExportableMixin, AutoSlugMixin, ImageAttachmentMixin, FileAttachmentMixin, CloneableMixin, SoftDeleteMixin, BookmarkableMixin
from core.managers import SoftDeleteManager, AllObjectsManager
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.utils.text import slugify
from extras.models import Tag


class Provider(AutoSlugMixin, JournalingMixin, TaggableMixin, ExportableMixin, ChangeLoggingMixin, BaseModel):
    """Represents the vendor/supplier of a subscription or service."""
    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Unique name of the provider (e.g., Adobe Inc.)"
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="URL-friendly identifier (auto-generated from name if left blank)"
    )
    account_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Account ID",
        help_text="Optional customer account number with the provider"
    )
    portal_url = models.URLField(
        blank=True,
        verbose_name="Admin Portal URL",
        help_text="URL for the provider's management/administration portal"
    )
    website = models.URLField(
        blank=True,
        verbose_name="Company Website",
        help_text="General public website of the provider"
    )
    contact_email = models.EmailField(
        blank=True,
        verbose_name="Contact Email",
        help_text="Primary support or account manager email"
    )
    contact_phone = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Contact Phone",
        help_text="Primary support phone number"
    )
    admin_notes = models.TextField(
        blank=True,
        verbose_name="Admin Notes",
        help_text="Optional internal administrative notes"
    )
    support_contact = models.TextField(
        blank=True,
        help_text="Additional support contact details (hours, escalation, etc.)"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Active",
        db_index=True,
        help_text="Deactivate to hide from selection lists without deleting"
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


class SubscriptionStatusChoices(models.TextChoices):
    ACTIVE = 'active', _('Active')
    EXPIRED = 'expired', _('Expired')
    CANCELLED = 'cancelled', _('Cancelled')
    PENDING = 'pending', _('Pending')
    SUSPENDED = 'suspended', _('Suspended')
    RENEWING = 'renewing', _('Renewing')


class BillingCycleChoices(models.TextChoices):
    MONTHLY = 'monthly', _('Monthly')
    QUARTERLY = 'quarterly', _('Quarterly')
    ANNUAL = 'annual', _('Annual')
    BIANNUAL = 'biannual', _('Biannual')
    MULTI_YEAR = 'multi_year', _('Multi-Year')
    ONETIME = 'onetime', _('One-Time')


class Subscription(AutoSlugMixin, JournalingMixin, TaggableMixin, ImageAttachmentMixin, FileAttachmentMixin, BookmarkableMixin, SoftDeleteMixin, CloneableMixin, ExportableMixin, ChangeLoggingMixin, BaseModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    """Represents a recurring service agreement (SaaS, Support, etc.)."""
    name = models.CharField(
        max_length=255,
        help_text="Descriptive name (e.g., Adobe Creative Cloud - All Apps (Team))"
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="URL-friendly identifier (auto-generated from name if left blank)"
    )
    provider = models.ForeignKey(
        to=Provider,
        on_delete=models.PROTECT,
        related_name='subscriptions'
    )
    type = models.CharField(
        max_length=50,
        choices=SubscriptionTypeChoices.choices,
        default=SubscriptionTypeChoices.SAAS,
        verbose_name="Subscription Type",
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatusChoices.choices,
        default=SubscriptionStatusChoices.ACTIVE,
        verbose_name="Status",
        db_index=True,
    )
    start_date = models.DateField(
        blank=True,
        null=True,
        verbose_name="Start Date",
        db_index=True,
    )
    renewal_date = models.DateField(
        blank=True,
        null=True,
        verbose_name="Next Renewal Date",
        db_index=True,
    )
    renewal_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Cost per renewal period"
    )
    currency = models.CharField(
        max_length=3,
        default='USD',
        blank=True,
        verbose_name="Currency",
        help_text="ISO 4217 currency code (USD, EUR, GBP, AUD, etc.)"
    )
    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycleChoices.choices,
        default=BillingCycleChoices.ANNUAL,
        blank=True,
        verbose_name="Billing Cycle",
    )
    term_months = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Term (Months)",
        help_text="Duration of the subscription term in months"
    )
    auto_renewal = models.BooleanField(
        default=False,
        verbose_name="Auto-Renewal",
        help_text="Whether this subscription renews automatically"
    )
    licensed_quantity = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Licensed Quantity",
        help_text="Number of seats/users/devices covered (for SaaS/support)"
    )
    contract_reference = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Contract Reference",
        help_text="Contract number, PO reference, or quote ID"
    )
    cost_center = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Cost Center / Budget Code",
        help_text="Financial tracking code for chargeback"
    )
    cancellation_date = models.DateField(
        blank=True,
        null=True,
        verbose_name="Cancellation Date",
        db_index=True,
    )
    owner = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_subscriptions',
        verbose_name="Owner",
        help_text="Person responsible for this subscription"
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
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='subscriptions_org',
        db_index=True,
    )

    class Meta:
        ordering = ('-renewal_date', 'provider', 'name')
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"

    def __str__(self):
        return f"{self.provider} - {self.name}"

    def get_absolute_url(self):
        try:
            return reverse('subscriptions:subscription_detail', kwargs={'pk': self.pk})
        except NoReverseMatch:
            return reverse('admin:subscriptions_subscription_change', args=[self.pk])

    @property
    def is_expired(self):
        """Check if the subscription is past its renewal date."""
        if self.renewal_date:
            from django.utils import timezone
            return self.renewal_date < timezone.now().date()
        return False

    @property
    def days_until_renewal(self):
        """Number of days until the next renewal. Negative if overdue."""
        if self.renewal_date:
            from django.utils import timezone
            return (self.renewal_date - timezone.now().date()).days
        return None

    @property
    def annual_cost(self):
        """Estimated annual cost based on billing cycle."""
        if self.renewal_cost is None:
            return None
        if self.billing_cycle == BillingCycleChoices.MONTHLY:
            return self.renewal_cost * 12
        elif self.billing_cycle == BillingCycleChoices.QUARTERLY:
            return self.renewal_cost * 4
        elif self.billing_cycle == BillingCycleChoices.BIANNUAL:
            return self.renewal_cost * 2
        return self.renewal_cost

    def renew(self, new_renewal_date, cost=None):
        self.renewal_date = new_renewal_date
        if cost is not None:
            self.renewal_cost = cost
        self.status = SubscriptionStatusChoices.ACTIVE
        self.save(update_fields=['renewal_date', 'renewal_cost', 'status'])

    def cancel(self, cancellation_date=None, reason=''):
        self.cancellation_date = cancellation_date or timezone.now().date()
        self.status = SubscriptionStatusChoices.CANCELLED
        if reason:
            existing = self.notes or ''
            self.notes = f"{existing}\n[{timezone.now().date()}] Cancelled: {reason}".strip()
        self.save(update_fields=['cancellation_date', 'status', 'notes'])

    def suspend(self):
        self.status = SubscriptionStatusChoices.SUSPENDED
        self.save(update_fields=['status'])


class SubscriptionAssignment(ChangeLoggingMixin, BaseModel):
    """Flexibly links a Subscription to the entity (or entities) it covers."""
    subscription = models.ForeignKey(
        to=Subscription,
        on_delete=models.CASCADE,
        related_name='assignments'
    )
    content_type = models.ForeignKey(
        to=ContentType,
        on_delete=models.CASCADE,
        limit_choices_to={
            'model__in': ('asset', 'assetholder', 'location', 'consumable', 'accessory'),
        }
    )
    object_id = models.PositiveBigIntegerField()
    assigned_object = GenericForeignKey(
        ct_field='content_type',
        fk_field='object_id'
    )
    assigned_date = models.DateTimeField(
        auto_now_add=True,
        editable=False
    )
    assigned_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscription_assignments_created',
        verbose_name="Assigned By",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-assigned_date',)
        verbose_name = "Subscription Assignment"
        verbose_name_plural = "Subscription Assignments"
        constraints = [
            models.UniqueConstraint(
                fields=['subscription', 'content_type', 'object_id'],
                name='subscriptions_assignment_unique'
            )
        ]

    def __str__(self):
        if self.assigned_object:
            return f"Subscription {self.subscription} -> {self.assigned_object}"
        return f"Subscription {self.subscription} assignment (unlinked)"

    def get_absolute_url(self):
        if self.subscription:
            return self.subscription.get_absolute_url()
        try:
            return reverse('admin:subscriptions_subscriptionassignment_changelist')
        except NoReverseMatch:
            return "#"
