"""Catalog models: StatusLabel, AssetRole, Manufacturer, Depreciation, AssetType,
Supplier, Category — shared reference data that assets point into.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.contrib.contenttypes.fields import GenericRelation

from core.models import StandardModel
from core.mixins import AutoSlugMixin, SoftDeleteMixin, CustomFieldDataMixin
from core.managers import SoftDeleteManager, AllObjectsManager
from extras.models import CustomFieldset
from assets.choices import StatusTypeChoices


class StatusLabel(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    # Back-compat aliases — canonical definitions live in assets.choices.
    TYPE_DEPLOYABLE = StatusTypeChoices.DEPLOYABLE
    TYPE_DEPLOYED = StatusTypeChoices.DEPLOYED
    TYPE_PENDING = StatusTypeChoices.PENDING
    TYPE_UNDEPLOYABLE = StatusTypeChoices.UNDEPLOYABLE
    TYPE_ARCHIVED = StatusTypeChoices.ARCHIVED
    TYPE_IN_REPAIR = StatusTypeChoices.IN_REPAIR
    TYPE_ON_ORDER = StatusTypeChoices.ON_ORDER
    TYPE_CHOICES = StatusTypeChoices.choices

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    type = models.CharField(max_length=50, choices=StatusTypeChoices.choices, default=StatusTypeChoices.DEPLOYABLE, db_index=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=6, blank=True, help_text=_("RGB color in hexadecimal (e.g. 00ff00)"))
    tags = models.ManyToManyField('extras.Tag', related_name='status_labels_tagged', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Status Label")
        verbose_name_plural = _("Status Labels")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_statuslabel_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_statuslabel_slug_active'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def get_absolute_url(self):
        return reverse('assets:statuslabel_detail', kwargs={'pk': self.pk})


class AssetRole(StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    """Categorizes assets based on their functional role (e.g., Laptop, Monitor, Server)."""
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=6, blank=True, help_text=_("RGB color in hexadecimal (e.g. 00ff00)"))
    allows_components = models.BooleanField(
        default=False,
        help_text=_("Assets with this role can have components allocated (servers, workstations, …)"),
    )
    tags = models.ManyToManyField(
        to='extras.Tag',
        related_name='asset_roles',
        blank=True
    )

    class Meta:
        verbose_name = _("Asset Role")
        verbose_name_plural = _("Asset Roles")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_assetrole_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_assetrole_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        # Use standardized URL name
        return reverse('assets:assetrole_detail', args=[self.pk])


class Manufacturer(StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True)

    contacts = GenericRelation('organization.ContactAssignment')
    tags = models.ManyToManyField('extras.Tag', related_name='manufacturers', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Manufacturer")
        verbose_name_plural = _("Manufacturers")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_manufacturer_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_manufacturer_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:manufacturer_detail', kwargs={'pk': self.pk})

    @property
    def get_support_contact(self):
        """Resolves the active support contact assignment dynamically."""
        # 1. Search for a Contact assignment with role slug 'support' or 'technical-support'
        assignment = self.contacts.filter(role__slug__in=['support', 'technical-support']).first()
        if not assignment:
            # 2. Fallback to any assignment with 'primary' priority
            assignment = self.contacts.filter(priority='primary').first()
        if not assignment:
            # 3. Fallback to any contact assignment
            assignment = self.contacts.first()

        return assignment.contact if assignment else None


class Depreciation(StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Method(models.TextChoices):
        STRAIGHT_LINE = 'straight_line', _('Straight-line')
        NONE = 'none', _('None (no depreciation)')

    class Convention(models.TextChoices):
        EXCLUDE_PURCHASE_MONTH = 'exclude_purchase_month', _('Exclude purchase month (month diff)')
        INCLUDE_PURCHASE_MONTH = 'include_purchase_month', _('Include purchase month (pro rata temporis)')

    name = models.CharField(max_length=100, verbose_name=_("Depreciation Name"))
    months = models.PositiveIntegerField(
        verbose_name=_("Lifespan (Months)"),
        help_text=_("Useful lifespan in months for straight-line calculations"),
    )
    method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.STRAIGHT_LINE,
        verbose_name=_("Method"),
    )
    convention = models.CharField(
        max_length=30,
        choices=Convention.choices,
        default=Convention.INCLUDE_PURCHASE_MONTH,
        verbose_name=_("Convention"),
        help_text=_("Determines whether the acquisition month counts as a full depreciation month."),
    )
    immediate_expense_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Immediate expense threshold (GWG)"),
        help_text=_("Assets with purchase cost at or below this amount are fully expensed in the month of acquisition (e.g. 800 for German GWG)."),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Depreciation")
        verbose_name_plural = _("Depreciations")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_depreciation_name_active'),
        ]

    def __str__(self):
        return f"{self.name} ({self.months} months)"

    def get_absolute_url(self):
        return reverse('assets:depreciation_detail', kwargs={'pk': self.pk})


class AssetType(CustomFieldDataMixin, AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    """Defines a specific type of asset (e.g., a specific laptop model)."""
    slug_source = ('manufacturer__name', 'model')

    manufacturer = models.ForeignKey('assets.Manufacturer', on_delete=models.PROTECT, related_name='asset_types')
    model = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=255)
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text=_("Manufacturer part number or SKU"))

    eol_months = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("EOL (Months)"),
        help_text=_("Lifespan in months before EOL replacement")
    )
    custom_fieldset = models.ForeignKey(
        CustomFieldset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_types',
        verbose_name=_("Custom Fieldset")
    )
    # custom_field_data JSONField comes from CustomFieldDataMixin
    depreciation = models.ForeignKey(
        'assets.Depreciation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_types',
        verbose_name=_("Depreciation")
    )

    category = models.ForeignKey(
        'assets.Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_types',
        verbose_name=_("Category"),
        db_index=True
    )
    asset_role = models.ForeignKey(
        'assets.AssetRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_types',
        verbose_name=_("Asset Role"),
        db_index=True
    )
    image = models.ImageField(upload_to='asset_types/', blank=True, null=True, verbose_name=_("Model Image"))

    # Other
    description = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="asset_types", blank=True)
    requestable = models.BooleanField(default=False, db_index=True, help_text=_("Allow users to request assets of this type"))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['manufacturer', 'model'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_manufacturer_model_active',
            ),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_assettype_slug_active'),
        ]
        verbose_name = _("Asset Type")
        verbose_name_plural = _("Asset Types")

    def __str__(self):
        return f"{self.manufacturer.name} {self.model}"

    def get_absolute_url(self):
        return reverse('assets:assettype_detail', kwargs={'pk': self.pk})


class Supplier(CustomFieldDataMixin, AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    website = models.URLField(max_length=500, blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='suppliers', blank=True)
    contacts = GenericRelation('organization.ContactAssignment')

    @property
    def primary_contact(self):
        assignment = self.contacts.filter(priority='primary').first() or self.contacts.first()
        return assignment.contact if assignment else None

    class Meta:
        ordering = ['name']
        verbose_name = _("Supplier")
        verbose_name_plural = _("Suppliers")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_supplier_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_supplier_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:supplier_detail', kwargs={'pk': self.pk})


class Category(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    color = models.CharField(max_length=6, blank=True, help_text=_("RGB color in hexadecimal (e.g. 00ff00)"))
    description = models.TextField(blank=True)
    applies_to = models.JSONField(default=dict, blank=True, help_text=_("Applies to: {'asset': True, 'accessory': True, 'component': True}"))
    audit_interval_months = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=_("How often assets in this category must be physically audited, in months. Leave blank for no required cadence.")
    )
    tags = models.ManyToManyField('extras.Tag', related_name='categories', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_category_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_category_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:category_detail', kwargs={'pk': self.pk})
