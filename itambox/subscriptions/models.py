from django.db import models
from django.conf import settings
from django.urls import reverse, NoReverseMatch
from core.models import BaseModel, ChangeLoggingMixin, StandardModel, DeletableVaultModel
from core.currency import CurrencyField
from core.mixins import TaggableMixin, JournalingMixin, ExportableMixin, AutoSlugMixin, ImageAttachmentMixin, FileAttachmentMixin, CloneableMixin, SoftDeleteMixin, BookmarkableMixin, CustomFieldDataMixin
from core.managers import SoftDeleteManager, AllObjectsManager, TenantScopingSoftDeleteManager, TenantScopingManager, TenantScopingAllObjectsManager
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from extras.models import Tag


class Provider(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    """Represents the vendor/supplier of a subscription or service."""
    name = models.CharField(
        max_length=255,
        help_text=_("Unique name of the provider (e.g., Adobe Inc.)")
    )
    slug = models.SlugField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_("URL-friendly identifier (auto-generated from name if left blank)")
    )
    account_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Account ID"),
        help_text=_("Optional customer account number with the provider")
    )
    portal_url = models.URLField(
        blank=True,
        verbose_name=_("Admin Portal URL"),
        help_text=_("URL for the provider's management/administration portal")
    )
    admin_notes = models.TextField(
        blank=True,
        verbose_name=_("Admin Notes"),
        help_text=_("Optional internal administrative notes")
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
        db_index=True,
        help_text=_("Deactivate to hide from selection lists without deleting")
    )
    tags = models.ManyToManyField(
        to=Tag,
        blank=True,
        related_name='subscription_providers'
    )
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='subscription_providers',
        db_index=True,
        help_text=_("The tenant owning this provider. Null represents system-wide/global providers.")
    )
    tenant_group = models.ForeignKey(
        'organization.TenantGroup',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='subscription_providers',
        db_index=True,
        help_text=_("The tenant group owning this provider.")
    )
    contacts = GenericRelation('organization.ContactAssignment')

    @property
    def primary_contact(self):
        assignment = self.contacts.filter(priority='primary').first() or self.contacts.first()
        return assignment.contact if assignment else None

    class Meta:
        ordering = ('name',)
        verbose_name = _("Provider")
        verbose_name_plural = _("Providers")
        constraints = [
            models.CheckConstraint(
                check=models.Q(tenant__isnull=True) | models.Q(tenant_group__isnull=True),
                name='provider_tenant_or_group'
            ),
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                condition=models.Q(tenant__isnull=False) & models.Q(deleted_at__isnull=True),
                name='unique_tenant_provider_name'
            ),
            models.UniqueConstraint(
                fields=['tenant', 'slug'],
                condition=models.Q(tenant__isnull=False) & models.Q(deleted_at__isnull=True),
                name='unique_tenant_provider_slug'
            ),
            models.UniqueConstraint(
                fields=['tenant_group', 'name'],
                condition=models.Q(tenant_group__isnull=False) & models.Q(deleted_at__isnull=True),
                name='unique_tenant_group_provider_name'
            ),
            models.UniqueConstraint(
                fields=['tenant_group', 'slug'],
                condition=models.Q(tenant_group__isnull=False) & models.Q(deleted_at__isnull=True),
                name='unique_tenant_group_provider_slug'
            ),
            models.UniqueConstraint(
                fields=['name'],
                condition=models.Q(tenant__isnull=True) & models.Q(tenant_group__isnull=True) & models.Q(deleted_at__isnull=True),
                name='unique_global_provider_name'
            ),
            models.UniqueConstraint(
                fields=['slug'],
                condition=models.Q(tenant__isnull=True) & models.Q(tenant_group__isnull=True) & models.Q(deleted_at__isnull=True),
                name='unique_global_provider_slug'
            )
        ]

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
    TRIAL = 'trial', _('Trial')


class BillingCycleChoices(models.TextChoices):
    MONTHLY = 'monthly', _('Monthly')
    QUARTERLY = 'quarterly', _('Quarterly')
    ANNUAL = 'annual', _('Annual')
    BIANNUAL = 'biannual', _('Biannual')
    MULTI_YEAR = 'multi_year', _('Multi-Year')
    ONETIME = 'onetime', _('One-Time')


class Subscription(CustomFieldDataMixin, AutoSlugMixin, BookmarkableMixin, DeletableVaultModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    """Represents a recurring service agreement (SaaS, Support, etc.)."""
    name = models.CharField(
        max_length=255,
        help_text=_("Descriptive name (e.g., Adobe Creative Cloud - All Apps (Team))")
    )
    slug = models.SlugField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_("URL-friendly identifier (auto-generated from name if left blank)")
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
        verbose_name=_("Subscription Type"),
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatusChoices.choices,
        default=SubscriptionStatusChoices.ACTIVE,
        verbose_name=_("Status"),
        db_index=True,
    )
    start_date = models.DateField(
        blank=True,
        null=True,
        verbose_name=_("Start Date"),
        db_index=True,
    )
    renewal_date = models.DateField(
        blank=True,
        null=True,
        verbose_name=_("Next Renewal Date"),
        db_index=True,
    )
    renewal_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text=_("Cost per renewal period")
    )
    currency = CurrencyField()
    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycleChoices.choices,
        default=BillingCycleChoices.ANNUAL,
        blank=True,
        verbose_name=_("Billing Cycle"),
    )
    term_months = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_("Term (Months)"),
        help_text=_("Duration of the subscription term in months")
    )
    auto_renewal = models.BooleanField(
        default=True,
        verbose_name=_("Auto-Renewal"),
        help_text=_("Whether this subscription renews automatically")
    )
    licensed_quantity = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_("Licensed Quantity"),
        help_text=_("Number of seats/users/devices covered (for SaaS/support)")
    )
    contract_reference = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Contract Reference"),
        help_text=_("Contract number, PO reference, or quote ID")
    )
    cost_center = models.ForeignKey(
        'organization.CostCenter',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions',
        verbose_name=_("Cost Center"),
        help_text=_("Financial cost center responsible for this subscription"),
        db_index=True,
    )
    cancellation_date = models.DateField(
        blank=True,
        null=True,
        verbose_name=_("Cancellation Date"),
        db_index=True,
    )
    owner = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_subscriptions',
        verbose_name=_("Owner"),
        help_text=_("Person responsible for this subscription")
    )
    description = models.TextField(
        blank=True,
        help_text=_("Optional text detailing coverage or terms")
    )
    notes = models.TextField(
        blank=True,
        help_text=_("Optional internal notes")
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
        verbose_name = _("Subscription")
        verbose_name_plural = _("Subscriptions")
        constraints = [
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_subscription_slug_active'),
        ]

    def __str__(self):
        return f"{self.provider} - {self.name}"

    @property
    def total_seats(self):
        """Total entitled seats across all licenses funded by this subscription."""
        from django.db.models import Sum
        return self.licenses.filter(deleted_at__isnull=True).aggregate(
            total=Sum('seats')
        )['total'] or 0

    @property
    def assigned_seats(self):
        """Seats currently assigned across this subscription's licenses."""
        from licenses.models import LicenseSeatAssignment
        return LicenseSeatAssignment.objects.filter(
            license__subscription=self,
            license__deleted_at__isnull=True,
            deleted_at__isnull=True,
        ).count()

    @property
    def available_seats(self):
        """Unassigned seats across this subscription's licenses."""
        return max(0, self.total_seats - self.assigned_seats)

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
    tenant_lookup = 'subscription__tenant'
    # Subscriptions are always tenant-owned; a global (tenant=None) parent would
    # be an anomaly, so never expose its assignments cross-tenant.
    deny_global_tenant = True
    objects = TenantScopingManager()

    """Flexibly links a Subscription to the entity (or entities) it covers."""
    subscription = models.ForeignKey(
        to=Subscription,
        on_delete=models.CASCADE,
        related_name='assignments'
    )

    @property
    def tenant(self):
        return self.subscription.tenant if self.subscription_id else None
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
        verbose_name=_("Assigned By"),
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-assigned_date',)
        verbose_name = _("Subscription Assignment")
        verbose_name_plural = _("Subscription Assignments")
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
