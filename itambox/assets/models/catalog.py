"""Catalog models: StatusLabel, AssetRole, Manufacturer, Depreciation, AssetType,
Supplier, Category — shared reference data that assets point into.
"""

from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import connection, models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from assets.choices import StatusTypeChoices
from core.managers import AllObjectsManager, SoftDeleteManager
from core.mixins import AutoSlugMixin, CustomFieldDataMixin, SoftDeleteMixin
from core.models import BaseModel, ChangeLoggingMixin, StandardModel
from extras.models import CustomFieldset

_LIBRARY_NAMESPACE_VALIDATOR = RegexValidator(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
_LIBRARY_DEFINITION_KEY_VALIDATOR = RegexValidator(r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")
_LIBRARY_RELEASE_VALIDATOR = RegexValidator(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")
_SOURCE_CHECKSUM_VALIDATOR = RegexValidator(r"^sha256:[0-9a-f]{64}$")


def _asset_type_field_validation_errors(asset_type):
    errors = {}
    for field_name, value, validator in (
        ("library_definition_key", asset_type.library_definition_key, _LIBRARY_DEFINITION_KEY_VALIDATOR),
        ("library_release", asset_type.library_release, _LIBRARY_RELEASE_VALIDATOR),
        ("source_checksum", asset_type.source_checksum, _SOURCE_CHECKSUM_VALIDATOR),
    ):
        if value is None:
            continue
        try:
            validator(value)
        except ValidationError as exc:
            errors[field_name] = exc.messages
    return errors


def _asset_type_identity_errors(asset_type):
    errors = {}
    has_library_identity = any(
        value not in (None, "")
        for value in (
            asset_type.library_id,
            asset_type.library_definition_key,
            asset_type.library_release,
            asset_type.source_checksum,
        )
    )
    if asset_type.management_kind == "library":
        if asset_type.library_id is None:
            errors["library"] = _("Library-managed Asset Types require a Library.")
        if not asset_type.library_definition_key:
            errors["library_definition_key"] = _("Library-managed Asset Types require a non-empty definition key.")
        if not asset_type.library_release:
            errors["library_release"] = _("Library-managed Asset Types require a release.")
    elif has_library_identity:
        errors["management_kind"] = _("Only library-managed Asset Types may carry Library identity or provenance.")
    return errors


class StatusLabel(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
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

    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    type = models.CharField(
        max_length=50,
        choices=StatusTypeChoices.choices,
        default=StatusTypeChoices.DEPLOYABLE,
        db_index=True,
        verbose_name=_("Type"),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    color = models.CharField(
        max_length=6, blank=True, verbose_name=_("Color"), help_text=_("RGB color in hexadecimal (e.g. 00ff00)")
    )
    tags = models.ManyToManyField("extras.Tag", related_name="status_labels_tagged", blank=True, verbose_name=_("Tags"))

    class Meta:
        ordering = ["name"]
        verbose_name = _("Status Label")
        verbose_name_plural = _("Status Labels")
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted_at__isnull=True), name="unique_statuslabel_name_active"
            ),
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(deleted_at__isnull=True), name="unique_statuslabel_slug_active"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def get_absolute_url(self):
        return reverse("assets:statuslabel_detail", kwargs={"pk": self.pk})


class AssetRole(StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    """Categorizes assets based on their functional role (e.g., Laptop, Monitor, Server)."""
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    color = models.CharField(
        max_length=6, blank=True, verbose_name=_("Color"), help_text=_("RGB color in hexadecimal (e.g. 00ff00)")
    )
    allows_components = models.BooleanField(
        default=False,
        verbose_name=_("Allows Components"),
        help_text=_("Assets with this role can have components allocated (servers, workstations, …)"),
    )
    tags = models.ManyToManyField(to="extras.Tag", related_name="asset_roles", blank=True, verbose_name=_("Tags"))

    class Meta:
        verbose_name = _("Asset Role")
        verbose_name_plural = _("Asset Roles")
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted_at__isnull=True), name="unique_assetrole_name_active"
            ),
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(deleted_at__isnull=True), name="unique_assetrole_slug_active"
            ),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        # Use standardized URL name
        return reverse("assets:assetrole_detail", args=[self.pk])


class Manufacturer(StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255, verbose_name=_("Name"))
    slug = models.SlugField(max_length=255, verbose_name=_("Slug"))
    description = models.TextField(blank=True, verbose_name=_("Description"))

    contacts = GenericRelation("organization.ContactAssignment")
    tags = models.ManyToManyField("extras.Tag", related_name="manufacturers", blank=True, verbose_name=_("Tags"))

    class Meta:
        ordering = ["name"]
        verbose_name = _("Manufacturer")
        verbose_name_plural = _("Manufacturers")
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted_at__isnull=True), name="unique_manufacturer_name_active"
            ),
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(deleted_at__isnull=True), name="unique_manufacturer_slug_active"
            ),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("assets:manufacturer_detail", kwargs={"pk": self.pk})

    @property
    def get_support_contact(self):
        """Resolves the active support contact assignment dynamically."""
        # 1. Search for a Contact assignment with role slug 'support' or 'technical-support'
        assignment = self.contacts.filter(role__slug__in=["support", "technical-support"]).first()
        if not assignment:
            # 2. Fallback to any assignment with 'primary' priority
            assignment = self.contacts.filter(priority="primary").first()
        if not assignment:
            # 3. Fallback to any contact assignment
            assignment = self.contacts.first()

        return assignment.contact if assignment else None


class Depreciation(StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Method(models.TextChoices):
        STRAIGHT_LINE = "straight_line", _("Straight-line")
        NONE = "none", _("None (no depreciation)")

    class Convention(models.TextChoices):
        EXCLUDE_PURCHASE_MONTH = "exclude_purchase_month", _("Exclude purchase month (month diff)")
        INCLUDE_PURCHASE_MONTH = "include_purchase_month", _("Include purchase month (pro rata temporis)")

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
        help_text=_(
            "Assets with purchase cost at or below this amount are fully expensed in the month of acquisition (e.g. 800 for German GWG)."
        ),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))

    class Meta:
        ordering = ["name"]
        verbose_name = _("Depreciation")
        verbose_name_plural = _("Depreciations")
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted_at__isnull=True), name="unique_depreciation_name_active"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.months} months)"

    def get_absolute_url(self):
        return reverse("assets:depreciation_detail", kwargs={"pk": self.pk})


class AssetTypeLibrary(ChangeLoggingMixin, BaseModel):
    changelog_global = True

    namespace = models.CharField(max_length=62, unique=True, validators=[_LIBRARY_NAMESPACE_VALIDATOR])
    release = models.CharField(max_length=64, validators=[_LIBRARY_RELEASE_VALIDATOR])
    source_checksum = models.CharField(
        max_length=71,
        null=True,
        blank=True,
        validators=[_SOURCE_CHECKSUM_VALIDATOR],
    )
    installed_at = models.DateTimeField(default=timezone.now)
    managed_paths = models.JSONField(default=dict, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["namespace"]

    def clean(self):
        super().clean()
        errors = {}
        for field_name, value, validator in (
            ("namespace", self.namespace, _LIBRARY_NAMESPACE_VALIDATOR),
            ("release", self.release, _LIBRARY_RELEASE_VALIDATOR),
            ("source_checksum", self.source_checksum, _SOURCE_CHECKSUM_VALIDATOR),
        ):
            if value is None and field_name == "source_checksum":
                continue
            try:
                validator(value)
            except ValidationError as exc:
                errors[field_name] = exc.messages
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.namespace


class AssetType(CustomFieldDataMixin, AutoSlugMixin, StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    soft_delete_preserve_references = True
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    """Defines a specific type of asset (e.g., a specific laptop model)."""
    slug_source = ("manufacturer__name", "model")

    MANAGEMENT_CORE = "core"
    MANAGEMENT_LIBRARY = "library"
    MANAGEMENT_LOCAL = "local"
    MANAGEMENT_KIND_CHOICES = [
        (MANAGEMENT_CORE, _("Core")),
        (MANAGEMENT_LIBRARY, _("Library")),
        (MANAGEMENT_LOCAL, _("Local")),
    ]
    LIFECYCLE_ACTIVE = "active"
    LIFECYCLE_DEPRECATED = "deprecated"
    LIFECYCLE_CHOICES = [
        (LIFECYCLE_ACTIVE, _("Active")),
        (LIFECYCLE_DEPRECATED, _("Deprecated")),
    ]

    manufacturer = models.ForeignKey(
        "assets.Manufacturer", on_delete=models.PROTECT, related_name="asset_types", verbose_name=_("Manufacturer")
    )
    model = models.CharField(max_length=255, db_index=True, verbose_name=_("Model"))
    slug = models.SlugField(max_length=255, verbose_name=_("Slug"))
    part_number = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name=_("Part Number"),
        help_text=_("Manufacturer part number or SKU"),
    )
    ean = models.CharField(
        max_length=14,
        blank=True,
        db_index=True,
        verbose_name=_("EAN"),
        help_text=_("Barcode (EAN / UPC / GTIN) — scanning shows assets of this type."),
    )
    region = models.CharField(max_length=64, blank=True, default="")
    configuration = models.CharField(max_length=255, blank=True, default="")
    management_kind = models.CharField(max_length=16, choices=MANAGEMENT_KIND_CHOICES, default=MANAGEMENT_LOCAL)
    library = models.ForeignKey(
        AssetTypeLibrary,
        on_delete=models.PROTECT,
        related_name="asset_types",
        null=True,
        blank=True,
    )
    library_definition_key = models.CharField(
        max_length=127,
        null=True,
        blank=True,
        validators=[_LIBRARY_DEFINITION_KEY_VALIDATOR],
    )
    library_release = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        validators=[_LIBRARY_RELEASE_VALIDATOR],
    )
    source_checksum = models.CharField(
        max_length=71,
        null=True,
        blank=True,
        validators=[_SOURCE_CHECKSUM_VALIDATOR],
    )
    managed_paths = models.JSONField(default=dict, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    lifecycle = models.CharField(max_length=16, choices=LIFECYCLE_CHOICES, default=LIFECYCLE_ACTIVE)
    deprecated_at = models.DateTimeField(null=True, blank=True)
    replaced_by = models.CharField(max_length=190, null=True, blank=True)

    eol_months = models.PositiveIntegerField(
        null=True, blank=True, verbose_name=_("EOL (Months)"), help_text=_("Lifespan in months before EOL replacement")
    )
    custom_fieldsets = models.ManyToManyField(
        CustomFieldset,
        related_name="composed_asset_types",
        through="AssetTypeFieldset",
        blank=True,
    )
    # custom_field_data JSONField comes from CustomFieldDataMixin
    depreciation = models.ForeignKey(
        "assets.Depreciation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_types",
        verbose_name=_("Depreciation"),
    )

    category = models.ForeignKey(
        "assets.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_types",
        verbose_name=_("Category"),
        db_index=True,
    )
    asset_role = models.ForeignKey(
        "assets.AssetRole",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_types",
        verbose_name=_("Asset Role"),
        db_index=True,
    )
    image = models.ImageField(upload_to="asset_types/", blank=True, null=True, verbose_name=_("Model Image"))

    # Other
    description = models.TextField(blank=True, verbose_name=_("Description"))
    comments = models.TextField(blank=True, verbose_name=_("Comments"))
    tags = models.ManyToManyField("extras.Tag", related_name="asset_types", blank=True, verbose_name=_("Tags"))
    requestable = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name=_("Requestable"),
        help_text=_("Allow users to request assets of this type"),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(deleted_at__isnull=True), name="unique_assettype_slug_active"
            ),
            models.UniqueConstraint(
                fields=["library", "library_definition_key"],
                condition=models.Q(library__isnull=False),
                name="unique_assettype_library_identity",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(library__isnull=True, library_definition_key__isnull=True)
                    | models.Q(library__isnull=False, library_definition_key__isnull=False)
                ),
                name="assettype_library_identity_complete",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(management_kind="library")
                        & models.Q(library__isnull=False)
                        & models.Q(library_definition_key__isnull=False)
                        & ~models.Q(library_definition_key="")
                        & models.Q(library_release__isnull=False)
                        & ~models.Q(library_release="")
                    )
                    | (
                        models.Q(management_kind__in=["core", "local"])
                        & models.Q(library__isnull=True)
                        & models.Q(library_definition_key__isnull=True)
                        & models.Q(library_release__isnull=True)
                        & models.Q(source_checksum__isnull=True)
                    )
                ),
                name="assettype_management_library_coherence",
            ),
        ]
        verbose_name = _("Asset Type")
        verbose_name_plural = _("Asset Types")

    def __str__(self):
        return f"{self.manufacturer.name} {self.model}"

    def get_absolute_url(self):
        return reverse("assets:assettype_detail", kwargs={"pk": self.pk})

    def restore(self):
        """Restore an Asset Type and normalize the pre-cutover deleted state."""
        update_fields = ["deleted_at"]
        self.deleted_at = None
        if self.lifecycle == "deleted":
            self.lifecycle = self.LIFECYCLE_DEPRECATED
            update_fields.append("lifecycle")
        self.save(update_fields=update_fields)

    def clean(self):
        super().clean()
        errors = {
            **_asset_type_field_validation_errors(self),
            **_asset_type_identity_errors(self),
        }
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values("library_id", "library_definition_key", "library_release", "source_checksum")
                .first()
            )
            if previous:
                if any(
                    previous[field_name] != getattr(self, field_name)
                    for field_name in ("library_id", "library_definition_key")
                ):
                    raise ValidationError(
                        {
                            "library_definition_key": _("The library Asset Type identity is immutable after creation."),
                        }
                    )
                if not getattr(self, "_reconcile_library_state", False) and any(
                    previous[field_name] != getattr(self, field_name)
                    for field_name in ("library_release", "source_checksum")
                ):
                    raise ValidationError(
                        {
                            "library_release": _(
                                "The library Asset Type release and checksum may only change "
                                "through the library reconciliation path."
                            ),
                        }
                    )
        return super().save(*args, **kwargs)

    def _library_reconciliation_preflight_errors(self, library_release):
        errors = {}
        if self.management_kind != self.MANAGEMENT_LIBRARY:
            errors["management_kind"] = _("Only library-managed Asset Types may be reconciled.")
        if self.library_id is None or not self.library_definition_key:
            errors["library_definition_key"] = _(
                "Library-managed Asset Types require full library identity before reconciliation."
            )
        if not library_release:
            errors["library_release"] = _("The reconciled library release must not be empty.")
        if errors:
            raise ValidationError(errors)

    def _validate_library_reconciliation_values(self, library_release, source_checksum):
        for field_name, value in (
            ("library_release", library_release),
            ("source_checksum", source_checksum),
        ):
            if value is None:
                continue
            field = self._meta.get_field(field_name)
            try:
                field.run_validators(value)
            except ValidationError as exc:
                raise ValidationError({field_name: exc.messages}) from None

    def apply_library_reconciliation(self, *, library_release, source_checksum, reconciled_at=None):
        """Apply a controlled library reconciliation update.

        ``library_id`` and ``library_definition_key`` are immutable source
        identity and never change here. ``library_release`` and
        ``source_checksum`` are controlled reconciliation state: this method is
        the single supported path that may update them. The model save guard and
        the PostgreSQL trigger enforce the same boundary for ordinary saves and
        for direct QuerySet/SQL writes (the trigger opts the write in through
        the transaction-local ``itambox.assettype_reconcile`` setting).
        """
        self._library_reconciliation_preflight_errors(library_release)
        self._validate_library_reconciliation_values(library_release, source_checksum)
        self.library_release = library_release
        self.source_checksum = source_checksum
        if reconciled_at is not None:
            self.last_reconciled_at = reconciled_at
        self._reconcile_library_state = True
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('itambox.assettype_reconcile', 'on', true)")
                update_fields = ["library_release", "source_checksum"]
                if reconciled_at is not None:
                    update_fields.append("last_reconciled_at")
                self.save(update_fields=update_fields)
        finally:
            self._reconcile_library_state = False
        return self


class AssetTypeFieldset(BaseModel):
    asset_type = models.ForeignKey(AssetType, on_delete=models.CASCADE, related_name="fieldset_memberships")
    fieldset = models.ForeignKey(CustomFieldset, on_delete=models.PROTECT, related_name="asset_type_memberships")
    position = models.PositiveIntegerField()

    class Meta:
        ordering = ["position", "fieldset__namespace", "fieldset__slug"]
        constraints = [
            models.UniqueConstraint(fields=["asset_type", "fieldset"], name="unique_assettype_fieldset"),
            models.UniqueConstraint(fields=["asset_type", "position"], name="unique_assettype_fieldset_position"),
            models.CheckConstraint(
                condition=models.Q(position__gte=1, position__lte=1000000),
                name="assettype_fieldset_position_range",
            ),
        ]

    def __str__(self):
        return f"{self.asset_type}: {self.fieldset}"


class Supplier(CustomFieldDataMixin, AutoSlugMixin, StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255, verbose_name=_("Name"))
    slug = models.SlugField(max_length=255, verbose_name=_("Slug"))
    website = models.URLField(max_length=500, blank=True, verbose_name=_("Website"))
    address = models.TextField(blank=True, verbose_name=_("Address"))
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    tags = models.ManyToManyField("extras.Tag", related_name="suppliers", blank=True, verbose_name=_("Tags"))
    contacts = GenericRelation("organization.ContactAssignment")

    @property
    def primary_contact(self):
        assignment = self.contacts.filter(priority="primary").first() or self.contacts.first()
        return assignment.contact if assignment else None

    class Meta:
        ordering = ["name"]
        verbose_name = _("Supplier")
        verbose_name_plural = _("Suppliers")
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted_at__isnull=True), name="unique_supplier_name_active"
            ),
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(deleted_at__isnull=True), name="unique_supplier_slug_active"
            ),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("assets:supplier_detail", kwargs={"pk": self.pk})


class Category(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    soft_delete_preserve_references = True
    name = models.CharField(max_length=255, verbose_name=_("Name"))
    slug = models.SlugField(max_length=255, verbose_name=_("Slug"))
    color = models.CharField(
        max_length=6, blank=True, verbose_name=_("Color"), help_text=_("RGB color in hexadecimal (e.g. 00ff00)")
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    applies_to = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Applies To"),
        help_text=_("Applies to: {'asset': True, 'accessory': True, 'component': True}"),
    )
    audit_interval_months = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Audit Interval (Months)"),
        help_text=_(
            "How often assets in this category must be physically audited, in months. Leave blank for no required cadence."
        ),
    )
    tags = models.ManyToManyField("extras.Tag", related_name="categories", blank=True, verbose_name=_("Tags"))
    default_custom_fieldsets = models.ManyToManyField(
        CustomFieldset,
        related_name="default_for_categories",
        through="CategoryDefaultFieldset",
        blank=True,
    )

    class Meta:
        ordering = ["name"]
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted_at__isnull=True), name="unique_category_name_active"
            ),
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(deleted_at__isnull=True), name="unique_category_slug_active"
            ),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("assets:category_detail", kwargs={"pk": self.pk})


class CategoryDefaultFieldset(BaseModel):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="default_fieldset_memberships")
    fieldset = models.ForeignKey(CustomFieldset, on_delete=models.PROTECT, related_name="category_default_memberships")
    position = models.PositiveIntegerField()

    class Meta:
        ordering = ["position", "fieldset__namespace", "fieldset__slug"]
        constraints = [
            models.UniqueConstraint(fields=["category", "fieldset"], name="unique_category_default_fieldset"),
            models.UniqueConstraint(fields=["category", "position"], name="unique_category_default_position"),
            models.CheckConstraint(
                condition=models.Q(position__gte=1, position__lte=1000000),
                name="category_default_position_range",
            ),
        ]

    def __str__(self):
        return f"{self.category}: {self.fieldset}"
