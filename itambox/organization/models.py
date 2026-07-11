from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core.validators import MinValueValidator


def _default_currency():
    return getattr(settings, 'ITAMBOX_DEFAULT_CURRENCY', 'EUR')
from core.models import BaseModel, ChangeLoggingMixin, StandardModel, VaultModel, DeletableVaultModel
from core.managers import TenantScopingManager, SoftDeleteManager, AllObjectsManager, TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager
from core.mixins import ExportableMixin, TaggableMixin, JournalingMixin, AutoSlugMixin, CloneableMixin, ImageAttachmentMixin, FileAttachmentMixin, BookmarkableMixin, SubscribableMixin, SoftDeleteMixin, CustomFieldDataMixin

# Create your models here.

class Location(CustomFieldDataMixin, SubscribableMixin, StandardModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    STATUS_PLANNED = 'planned'
    STATUS_STAGING = 'staging'
    STATUS_ACTIVE = 'active'
    STATUS_DECOMMISSIONING = 'decommissioning'
    STATUS_RETIRED = 'retired'
    STATUS_CHOICES = [
        (STATUS_PLANNED, _('Planned')),
        (STATUS_STAGING, _('Staging')),
        (STATUS_ACTIVE, _('Active')),
        (STATUS_DECOMMISSIONING, _('Decommissioning')),
        (STATUS_RETIRED, _('Retired')),
    ]

    site = models.ForeignKey(
        'Site', # Use string reference
        on_delete=models.PROTECT, # Or PROTECT if locations shouldn't be deleted when site is
        related_name='locations',
        db_index=True,
        verbose_name=_("Site")
        # null=True # REMOVED temporary null
        # No blank=True as per requirements
    )
    name = models.CharField(max_length=100, db_index=True, verbose_name=_("Name")) # Changed max_length based on Site/Region etc.
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
        verbose_name=_("Status")
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='children',
        blank=True,
        null=True,
        verbose_name=_("Parent")
    )
    tenant = models.ForeignKey(
        'Tenant',
        on_delete=models.PROTECT,
        related_name='locations',
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Tenant")
    )
    facility = models.CharField(max_length=100, blank=True, verbose_name=_("Facility"))
    description = models.TextField(blank=True, verbose_name=_("Description")) # Using TextField for potentially longer descriptions
    tags = models.ManyToManyField('extras.Tag', related_name="locations", blank=True, verbose_name=_("Tags"))

    class Meta:
        ordering = ['site', 'name']
        verbose_name = _("Location")
        verbose_name_plural = _("Locations")
        constraints = [
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_location_slug_active'),
        ]


    def __str__(self):
        # Consider showing parent hierarchy later if needed
        return self.name

    def get_absolute_url(self):
        return reverse('organization:location_detail', kwargs={'pk': self.pk})

class Region(StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='children',
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Parent")
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    tags = models.ManyToManyField('extras.Tag', related_name="regions", blank=True, verbose_name=_("Tags"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Region")
        verbose_name_plural = _("Regions")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_region_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_region_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:region_detail', kwargs={'pk': self.pk})

class SiteGroup(StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='children',
        blank=True,
        null=True,
        verbose_name=_("Parent")
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    tags = models.ManyToManyField('extras.Tag', related_name="sitegroups", blank=True, verbose_name=_("Tags"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Site Group")
        verbose_name_plural = _("Site Groups")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_sitegroup_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_sitegroup_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:sitegroup_detail', kwargs={'pk': self.pk})

class TenantGroup(StandardModel, SoftDeleteMixin):
    # Tenant-scoped via a dedicated branch in filter_by_tenant: a user sees the
    # groups containing a tenant they're a member of, plus those groups'
    # ancestors. Internal tenancy machinery (the descendant walk, middleware
    # group resolution) uses TenantGroup._base_manager to stay unscoped.
    objects = TenantScopingSoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='children',
        blank=True,
        null=True,
        verbose_name=_("Parent")
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    tags = models.ManyToManyField('extras.Tag', related_name="tenantgroups", blank=True, verbose_name=_("Tags"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Tenant Group")
        verbose_name_plural = _("Tenant Groups")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_tenantgroup_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_tenantgroup_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:tenantgroup_detail', kwargs={'pk': self.pk})

class Tenant(DeletableVaultModel, BookmarkableMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    group = models.ForeignKey(
        TenantGroup,
        on_delete=models.SET_NULL,
        related_name='tenants',
        blank=True,
        null=True,
        verbose_name=_("Group")
    )
    is_provider = models.BooleanField(
        default=False, db_index=True,
        verbose_name=_("Manages other tenants (MSP mode)"),
        help_text=_("Superuser-controlled. When set, this tenant can manage other tenants "
                    "and its members can be granted reach into them."),
    )
    managed_by = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        related_name='managed_tenants',
        blank=True, null=True,
        db_index=True,
        verbose_name=_("Managed by"),
        help_text=_("The managing (provider) tenant. Empty for standalone/root tenants."),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    comments = models.TextField(blank=True, verbose_name=_("Comments"))
    tags = models.ManyToManyField('extras.Tag', related_name="tenants", blank=True, verbose_name=_("Tags"))
    currency = models.CharField(
        max_length=3,
        default=_default_currency,
        verbose_name=_("Display currency"),
        help_text=_("ISO 4217 currency code used for value display (display only, no conversion)."),
    )
    default_depreciation = models.ForeignKey(
        'assets.Depreciation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tenants_defaulting',
        verbose_name=_("Default depreciation policy"),
        help_text=_("Fallback policy applied to all assets that have no type-level schedule and no per-asset override."),
    )
    changelog_retention_days = models.IntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name=_("Changelog retention override (days)"),
        help_text=_(
            "Overrides ITAMBOX_CHANGELOG_RETENTION_DAYS for this tenant's ObjectChange "
            "rows only. Blank uses the global setting. 0 = unlimited (legal hold -- this "
            "tenant's changelog is never pruned by prune_changelog)."
        ),
    )

    class Meta:
        ordering = ['name']
        verbose_name = _("Tenant")
        verbose_name_plural = _("Tenants")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_tenant_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_tenant_slug_active'),
        ]

    def clean(self):
        # Runs on every save via the global pre_save validator — keep it cheap.
        # Depth-1 management tree (decided 2026-07-10): a manager cannot itself be
        # managed, and a managed tenant cannot manage others.
        from django.core.exceptions import ValidationError
        super().clean()
        if self.managed_by_id:
            if self.managed_by_id == self.pk:
                raise ValidationError({'managed_by': _("A tenant cannot manage itself.")})
            if self.is_provider:
                raise ValidationError({'is_provider': _(
                    "A managed tenant cannot itself be a managing tenant (one level only)."
                )})
            manager = self.managed_by
            if not manager.is_provider:
                raise ValidationError({'managed_by': _(
                    "The managing tenant must have MSP mode enabled (is_provider)."
                )})
            if manager.managed_by_id:
                raise ValidationError({'managed_by': _(
                    "The managing tenant is itself managed — chains are not supported."
                )})

    def get_absolute_url(self):
        return reverse('organization:tenant_detail', kwargs={'pk': self.pk})

    def __str__(self):
        return self.name

class Site(DeletableVaultModel, BookmarkableMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    STATUS_PLANNED = 'planned'
    STATUS_STAGING = 'staging'
    STATUS_ACTIVE = 'active'
    STATUS_DECOMMISSIONING = 'decommissioning'
    STATUS_RETIRED = 'retired'
    STATUS_CHOICES = [
        (STATUS_PLANNED, _('Planned')),
        (STATUS_STAGING, _('Staging')),
        (STATUS_ACTIVE, _('Active')),
        (STATUS_DECOMMISSIONING, _('Decommissioning')),
        (STATUS_RETIRED, _('Retired')),
    ]

    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True, verbose_name=_("Status"))
    # Use local models for FKs within the app
    region = models.ForeignKey(Region, on_delete=models.SET_NULL, related_name='sites', blank=True, null=True, db_index=True, verbose_name=_("Region"))
    group = models.ForeignKey(SiteGroup, on_delete=models.SET_NULL, related_name='sites', blank=True, null=True, db_index=True, verbose_name=_("Group"))
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name='sites', blank=True, null=True, db_index=True, verbose_name=_("Tenant"))
    facility = models.CharField(max_length=100, blank=True, verbose_name=_("Facility"))
    time_zone = models.CharField(max_length=63, blank=True, verbose_name=_("Time Zone")) # Consider using timezone_field package later
    description = models.CharField(max_length=200, blank=True, verbose_name=_("Description"))
    physical_address = models.CharField(max_length=200, blank=True, verbose_name=_("Physical Address"))
    shipping_address = models.CharField(max_length=200, blank=True, verbose_name=_("Shipping Address"))
    latitude = models.DecimalField(max_digits=8, decimal_places=6, blank=True, null=True, verbose_name=_("Latitude"))
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True, verbose_name=_("Longitude"))
    comments = models.TextField(blank=True, verbose_name=_("Comments"))
    tags = models.ManyToManyField('extras.Tag', related_name="sites", blank=True, verbose_name=_("Tags"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Site")
        verbose_name_plural = _("Sites")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_site_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_site_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:site_detail', kwargs={'pk': self.pk})

# +++ AssetHolder Model +++
class AssetHolder(CustomFieldDataMixin, SubscribableMixin, StandardModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, # Keep holder if user is deleted, set user link to null
        related_name='asset_holder_profiles', # Custom related_name
        blank=True,
        null=True,
        verbose_name=_("User")
    )
    first_name = models.CharField(max_length=100, verbose_name=_("First Name"))
    last_name = models.CharField(max_length=100, verbose_name=_("Last Name"))
    upn = models.CharField(max_length=255, verbose_name=_('User Principal Name'))
    email = models.EmailField(blank=True, verbose_name=_("Email"))
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='asset_holders',
        db_index=True,
        verbose_name=_("Tenant")
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    comments = models.TextField(blank=True, verbose_name=_("Comments"))
    tags = models.ManyToManyField('extras.Tag', blank=True, related_name='organization_assetholders', verbose_name=_("Tags")) # M2M to extras.Tag

    class Meta:
        ordering = ['last_name', 'first_name', 'upn']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'user'], condition=models.Q(deleted_at__isnull=True), name='unique_tenant_user_profile'),
            models.UniqueConstraint(fields=['tenant', 'upn'], condition=models.Q(deleted_at__isnull=True), name='unique_tenant_upn'),
        ]
        verbose_name = _("Asset Holder")
        verbose_name_plural = _("Asset Holders")

    @property
    def checked_out_assets(self):
        from assets.models import AssetAssignment
        return AssetAssignment.objects.filter(
            assigned_user=self,
            is_active=True
        )

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.upn})"

    def get_absolute_url(self):
        # Assuming you'll have a detail view named 'assetholder_detail'
        return reverse('organization:assetholder_detail', kwargs={'pk': self.pk})



class ContactRole(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    changelog_global = True  # global reference data → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    description = models.TextField(blank=True, verbose_name=_("Description"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Contact Role")
        verbose_name_plural = _("Contact Roles")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_contactrole_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_contactrole_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:contactrole_detail', kwargs={'pk': self.pk})


class Contact(CustomFieldDataMixin, StandardModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    # Hybrid tenancy: tenant=None is a global/shared contact (manufacturers,
    # service desks) visible to every tenant; a set tenant makes the contact
    # private to that tenant. allow_global_tenant surfaces the tenant=None rows
    # under tenant scoping. Changelog attribution follows the `tenant` field
    # automatically (None → global, surfaced via ObjectChange.allow_global_tenant).
    allow_global_tenant = True

    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='contacts',
        db_index=True,
        verbose_name=_("Tenant"),
        help_text=_("Owning tenant. Leave blank for a global/shared contact "
                    "(e.g. a manufacturer or service desk) visible to all tenants."),
    )
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    title = models.CharField(max_length=100, blank=True, verbose_name=_("Title"))
    phone = models.CharField(max_length=50, blank=True, verbose_name=_("Phone"))
    email = models.EmailField(blank=True, verbose_name=_("Email"))
    web_url = models.URLField(blank=True, verbose_name=_("Web URL"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    comments = models.TextField(blank=True, verbose_name=_("Comments"))
    tags = models.ManyToManyField('extras.Tag', blank=True, related_name='organization_contacts', verbose_name=_("Tags"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Contact")
        verbose_name_plural = _("Contacts")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:contact_detail', kwargs={'pk': self.pk})


class ContactAssignment(ChangeLoggingMixin, BaseModel):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='assignments', verbose_name=_("Contact"))
    role = models.ForeignKey(ContactRole, on_delete=models.PROTECT, related_name='assignments', verbose_name=_("Role"))
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    assigned_object = GenericForeignKey('content_type', 'object_id')
    priority = models.CharField(
        max_length=50,
        choices=[
            ('primary', _('Primary')),
            ('secondary', _('Secondary')),
            ('tertiary', _('Tertiary')),
            ('inactive', _('Inactive')),
        ],
        blank=True,
        verbose_name=_("Priority"),
    )

    class Meta:
        ordering = ['contact', 'role', 'content_type', 'object_id']
        constraints = [
            models.UniqueConstraint(
                fields=['contact', 'role', 'content_type', 'object_id'],
                name='organization_contactassignment_unique'
            )
        ]
        verbose_name = _("Contact Assignment")
        verbose_name_plural = _("Contact Assignments")

    @property
    def tenant(self):
        # Contact is hybrid-tenant-scoped (a `tenant` field, tenant=None for
        # global/shared rows), but an assignment's own tenant is still derived
        # from the generic-FK target, not from `self.contact.tenant` — the
        # target is what StrictTenantPermission must bound the assignment to.
        # Exposing `tenant` lets StrictTenantPermission enforce the object-level
        # boundary on detail/mutation: it compares obj.tenant to the request's
        # active tenant. Targets with no tenant (global/shared catalogue rows)
        # return None and remain accessible, matching validate_gfk_target_tenant.
        obj = self.assigned_object
        return getattr(obj, 'tenant', None) if obj is not None else None

    def __str__(self):
        return f"{self.contact} ({self.role}) assigned to {self.assigned_object}"


# ---------------------------------------------------------------------------
# Unified RBAC: Role + Membership + RoleAssignment
#
# One container type (Tenant), one permission vocabulary ('app.codename'), and
# per-grant RoleAssignment rows. A provider (MSP) is just a tenant with
# ``is_provider=True`` that other tenants point at via ``managed_by``; reach
# into those managed tenants is a property of the individual grant.
# ---------------------------------------------------------------------------

class Role(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    """A named permission set owned by (and applying in) one tenant.

    A role owned by a managing (``is_provider``) tenant may additionally be
    *shared* with its managed tenants (``shared_with_managed``): managed-tenant
    admins can assign it to their own members but never edit it — a live shared
    definition, not a clone, so an edit at the owner propagates everywhere.
    """
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        related_name='roles',
        db_index=True,
        verbose_name=_("Tenant"),
    )
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    permissions = models.JSONField(
        default=list, blank=True,
        verbose_name=_("Permissions"),
        help_text=_("List of permission codenames ('app_label.codename')."),
    )
    shared_with_managed = models.BooleanField(
        default=False,
        verbose_name=_("Share with managed tenants"),
        help_text=_("Only meaningful when the owning tenant manages others: managed tenants "
                    "may assign this role to their own members; only the owning tenant can "
                    "edit it."),
    )

    class Meta:
        ordering = ['name']
        verbose_name = _("Role")
        verbose_name_plural = _("Roles")
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                condition=models.Q(deleted_at__isnull=True),
                name='organization_role_unique_tenant_name',
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.tenant.name})" if self.tenant_id else self.name

    def get_absolute_url(self):
        return reverse('organization:role_detail', kwargs={'pk': self.pk})

    @property
    def owner(self):
        """Compat alias: the tenant that owns this role (guards/forms call ``role.owner``)."""
        return self.tenant

    # NOTE: Role intentionally has NO model-layer ``clean()`` validation of ``permissions``.
    # A global ``pre_save`` receiver (core/signals.py::validate_custom_validators_on_save) runs
    # ``clean()`` on every ChangeLoggingMixin save, so a hard check here would fire on ALL
    # writes — incompatible with this codebase's deliberate design: RoleForm drops unknown
    # codenames, and ``validate_role_permissions`` audits persisted stale codenames post-hoc.
    # Real write-time integrity belongs in a future ManyToManyField(auth.Permission) migration.


class Membership(ChangeLoggingMixin, models.Model):
    """A user's binding to one tenant — the thin "person belongs here" anchor.

    What the person may DO is carried by :class:`RoleAssignment` rows hanging off
    this membership (one row per role × reach). ``is_active=False`` suspends the
    person in this tenant (all their assignments at once) while retaining the row,
    the assignments, and any AssetHolder linkage for re-activation.

    NOTE: the global ``User.is_active`` / ``User.can_login`` is synced from
    memberships ONLY by the SCIM provisioning paths, which clear it when a
    provisioned user has no active membership left. The interactive UI does NOT
    auto-clear it: a user whose last membership is deactivated stays authenticated
    but lands on the "no accessible workspace" page.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name=_("User"),
    )
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        related_name='memberships',
        db_index=True,
        verbose_name=_("Tenant"),
    )
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Active"))
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user']
        verbose_name = _("Membership")
        verbose_name_plural = _("Memberships")
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'tenant'],
                name='organization_membership_unique_user_tenant',
            ),
        ]

    def __str__(self):
        return f"{self.user} @ {self.tenant}"

    def get_absolute_url(self):
        return reverse('organization:membership_detail', kwargs={'pk': self.pk})

    @property
    def is_staff_membership(self):
        """True when any assignment on this membership projects into managed tenants."""
        return self.assignments.filter(reach=RoleAssignment.REACH_MANAGED).exists()


class RoleAssignment(ChangeLoggingMixin, models.Model):
    """One role granted at one scope — the unit of granting and audit.

    ``reach='own'``      → the role applies inside ``membership.tenant`` only.
    ``reach='managed'``  → the role applies inside the managed tenants selected by
                           the refinement (``managed_scope`` / ``scope_group`` /
                           ``assigned_tenants``); valid only when
                           ``membership.tenant.is_provider``.

    "Same role in both places" is two rows. Every grant records who granted it
    and when — the audit provenance the old roles-M2M could not carry.
    """
    # Attribute each grant/revoke to the membership's tenant in the changelog.
    changelog_tenant_lookup = 'membership__tenant'
    # A SOFT-deleted Role/Tenant must not physically destroy its grant rows:
    # they are the audit trail, the backend already treats deleted roles as
    # inert, and restoring the role re-arms the grants. Hard deletes still
    # cascade normally. (Consumed by SoftDeleteMixin.delete's collector loop.)
    survive_parent_soft_delete = True

    REACH_OWN = 'own'
    REACH_MANAGED = 'managed'
    REACH_CHOICES = [
        (REACH_OWN, _('This tenant')),
        (REACH_MANAGED, _('Managed tenants')),
    ]

    SCOPE_EXPLICIT = 'explicit'
    SCOPE_TENANT_GROUP = 'tenant_group'
    SCOPE_ALL = 'all'
    SCOPE_CHOICES = [
        (SCOPE_EXPLICIT, _('Specific tenants')),
        (SCOPE_TENANT_GROUP, _('A tenant group + its descendants')),
        (SCOPE_ALL, _('All managed tenants')),
    ]

    membership = models.ForeignKey(
        'organization.Membership',
        on_delete=models.CASCADE,
        related_name='assignments',
        verbose_name=_("Membership"),
    )
    role = models.ForeignKey(
        'organization.Role',
        on_delete=models.CASCADE,
        related_name='assignments',
        verbose_name=_("Role"),
    )
    reach = models.CharField(
        max_length=10, choices=REACH_CHOICES, default=REACH_OWN,
        db_index=True, verbose_name=_("Reach"),
    )
    managed_scope = models.CharField(
        max_length=20, choices=SCOPE_CHOICES, blank=True, null=True,
        verbose_name=_("Managed scope"),
        help_text=_("Managed reach only. Which managed tenants this grant covers."),
    )
    scope_group = models.ForeignKey(
        'organization.TenantGroup',
        on_delete=models.SET_NULL,
        related_name='assignment_scopes',
        blank=True, null=True,
        verbose_name=_("Scope group"),
        help_text=_("Used when managed_scope='tenant_group'."),
    )
    assigned_tenants = models.ManyToManyField(
        'organization.Tenant',
        related_name='reach_assignments',
        blank=True,
        verbose_name=_("Assigned tenants"),
        help_text=_("Used when managed_scope='explicit'."),
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='granted_assignments',
        verbose_name=_("Granted by"),
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['membership', 'role']
        verbose_name = _("Role assignment")
        verbose_name_plural = _("Role assignments")
        constraints = [
            models.UniqueConstraint(
                fields=['membership', 'role', 'reach'],
                name='organization_roleassignment_unique_grant',
            ),
        ]

    def __str__(self):
        return f"{self.membership.user} : {self.role.name} ({self.get_reach_display()})"

    def clean(self):
        # Runs on every save via the global pre_save validator — keep it cheap.
        from django.core.exceptions import ValidationError
        super().clean()
        if self.reach == self.REACH_MANAGED:
            if self.membership_id and not self.membership.tenant.is_provider:
                raise ValidationError({'reach': _(
                    "Managed reach requires the membership's tenant to be a managing "
                    "(provider) tenant."
                )})
            if not self.managed_scope:
                self.managed_scope = self.SCOPE_EXPLICIT
        else:
            if self.managed_scope or self.scope_group_id:
                raise ValidationError({'managed_scope': _(
                    "Scope refinement is only valid for managed reach."
                )})

    # ------------------------------------------------------------------ reach resolution
    # CANONICAL source of truth for managed-tenant reachability. The auth backend,
    # organization.access, and the access report all delegate here — do NOT hand-copy
    # the branching elsewhere.

    def covers_tenant(self, tenant):
        """Whether ``tenant`` falls within this managed-reach grant."""
        if self.reach != self.REACH_MANAGED:
            return False
        # A grant only ever covers tenants managed by its OWN membership tenant.
        if tenant.managed_by_id != self.membership.tenant_id:
            return False
        scope = self.managed_scope or self.SCOPE_EXPLICIT
        if scope == self.SCOPE_ALL:
            return True
        if scope == self.SCOPE_TENANT_GROUP:
            if not self.scope_group_id or not tenant.group_id:
                return False
            # inline import: avoid an organization.models <-> organization.access cycle at load
            from organization.access import get_descendant_tenant_group_ids
            return tenant.group_id in get_descendant_tenant_group_ids(self.scope_group_id)
        return self.assigned_tenants.filter(pk=tenant.pk).exists()

    def scoped_tenant_ids(self):
        """The set of managed-tenant ids this grant reaches (empty for own-reach)."""
        if self.reach != self.REACH_MANAGED:
            return set()
        provider_id = self.membership.tenant_id
        scope = self.managed_scope or self.SCOPE_EXPLICIT
        if scope == self.SCOPE_ALL:
            return set(
                Tenant._base_manager.filter(managed_by_id=provider_id)
                .values_list('pk', flat=True)
            )
        if scope == self.SCOPE_TENANT_GROUP:
            if not self.scope_group_id:
                return set()
            # inline import: avoid an organization.models <-> organization.access cycle at load
            from organization.access import get_descendant_tenant_group_ids
            group_ids = get_descendant_tenant_group_ids(self.scope_group_id)
            return set(
                Tenant._base_manager.filter(
                    managed_by_id=provider_id, group_id__in=group_ids,
                ).values_list('pk', flat=True)
            )
        return set(
            self.assigned_tenants.filter(managed_by_id=provider_id)
            .values_list('pk', flat=True)
        )


class CostCenter(AutoSlugMixin, CustomFieldDataMixin, StandardModel, SoftDeleteMixin):
    """
    Represents a cost center or department.  A top-level instance (parent=None)
    is a cost center; a child instance is a department within that cost center.
    The same model handles both via the optional self-referential parent.
    """
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    tenant = models.ForeignKey(
        'Tenant',
        on_delete=models.PROTECT,
        related_name='cost_centers',
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Tenant"),
    )
    name = models.CharField(max_length=100, db_index=True, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    code = models.CharField(
        max_length=50,
        db_index=True,
        help_text=_('Short identifier for this cost center (e.g. "CC-100").'),
        verbose_name=_("Code"),
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        related_name='children',
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Parent"),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Is Active"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Cost Center")
        verbose_name_plural = _("Cost Centers")
        constraints = [
            # code unique per active tenant (soft-delete-aware)
            models.UniqueConstraint(
                fields=['tenant', 'code'],
                condition=models.Q(deleted_at__isnull=True),
                name='organization_costcenter_unique_tenant_code_active',
            ),
            models.UniqueConstraint(
                fields=['slug'],
                condition=models.Q(deleted_at__isnull=True),
                name='organization_costcenter_unique_slug_active',
            ),
        ]

    def __str__(self):
        return f"{self.code} – {self.name}" if self.code else self.name

    def get_absolute_url(self):
        return reverse('organization:costcenter_detail', kwargs={'pk': self.pk})

    @property
    def depth(self):
        """Zero-based depth in the hierarchy (0 = top-level cost center)."""
        level = 0
        node = self
        while node.parent_id is not None:
            node = node.parent
            level += 1
            if level > 50:  # guard against accidental cycles in DB
                break
        return level

    @property
    def full_path(self):
        """Slash-joined name path from root to this node."""
        parts = [self.name]
        node = self
        visited = {self.pk}
        while node.parent_id is not None:
            node = node.parent
            if node.pk in visited:
                break
            visited.add(node.pk)
            parts.insert(0, node.name)
        return " / ".join(parts)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.parent_id is None:
            return
        # Self-parent guard
        if self.pk and self.parent_id == self.pk:
            raise ValidationError({'parent': _("A cost center cannot be its own parent.")})
        # Ancestor-cycle guard
        if self.pk:
            visited = set()
            node = self.parent
            while node is not None:
                if node.pk == self.pk:
                    raise ValidationError({'parent': _("Setting this parent would create a cycle in the hierarchy.")})
                if node.pk in visited:
                    break
                visited.add(node.pk)
                node = node.parent
