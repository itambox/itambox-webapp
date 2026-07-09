from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings


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
    # The MSP / managing organization this tenant belongs to. NULL for single-company
    # installs (no Provider rows) — the entire Provider layer is then invisible.
    provider = models.ForeignKey(
        'organization.Provider',
        on_delete=models.SET_NULL,
        related_name='tenants',
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Provider"),
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

    class Meta:
        ordering = ['name']
        verbose_name = _("Tenant")
        verbose_name_plural = _("Tenants")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_tenant_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_tenant_slug_active'),
        ]

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


import uuid
from django.utils import timezone
from django.db import transaction


# ---------------------------------------------------------------------------
# Unified RBAC: Role + Membership
#
# Roles and Memberships replace the prior six-model tangle (TenantRole,
# ProviderRole, ProviderRoleTemplate, TenantMembership, ProviderMembership,
# UserGroup-roles-pointer). They are differentiated by their container FK pair
# (``Role.scope`` / a Membership's tenant-vs-provider) rather than by separate models,
# so one form, one view, one auth-backend path covers every case.
# ---------------------------------------------------------------------------

class Role(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    """A named permission set bound to either a Tenant or a Provider.

    A *tenant-scoped* role (``scope='tenant'``, ``tenant`` set) lists Django
    permissions for use inside that tenant. A *provider-scoped* role
    (``scope='provider'``, ``provider`` set) lists permissions granted to MSP
    staff across the provider's tenants (per the Membership's tenant_scope),
    and may additionally carry provider-level capabilities such as
    ``organization.manage_tenants`` / ``manage_staff`` / ``manage_groups`` /
    ``manage_provider`` — these are plain Django permissions registered on the
    Provider model, so all gating flows through ``user.has_perm()``.
    """
    SCOPE_TENANT = 'tenant'
    SCOPE_PROVIDER = 'provider'
    SCOPE_CHOICES = [
        (SCOPE_TENANT, _('Tenant role')),
        (SCOPE_PROVIDER, _('Provider role')),
    ]

    # Tenant-scoped roles ride the standard tenant-scoping managers; provider-scoped
    # roles (``tenant=NULL``) ride through via ``allow_global_tenant`` so MSP staff
    # working in any of the provider's tenants still see them. Real per-tenant access
    # control sits in ``MembershipBackend``, not the manager.
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    # ChangeLoggingMixin must accept tenant=None entries (provider roles) when writing
    # ObjectChange rows.
    changelog_global = True

    scope = models.CharField(
        max_length=20,
        choices=SCOPE_CHOICES,
        default=SCOPE_TENANT,
        db_index=True,
        verbose_name=_("Scope"),
    )
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        related_name='roles',
        blank=True, null=True,
        db_index=True,
        verbose_name=_("Tenant"),
    )
    provider = models.ForeignKey(
        'organization.Provider',
        on_delete=models.CASCADE,
        related_name='roles',
        blank=True, null=True,
        db_index=True,
        verbose_name=_("Provider"),
    )
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    permissions = models.JSONField(
        default=list, blank=True,
        verbose_name=_("Permissions"),
        help_text=_(
            "List of permission codenames ('app_label.codename'). For provider-scoped "
            "roles, may include organization.manage_tenants/staff/groups/provider."
        ),
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name=_("Clone to new tenants"),
        help_text=_("Provider-scoped roles only. Automatically clone this role into new tenants created under this provider."),
    )

    class Meta:
        ordering = ['scope', 'name']
        verbose_name = _("Role")
        verbose_name_plural = _("Roles")
        constraints = [
            # Names unique within the owning tenant/provider.
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                condition=models.Q(deleted_at__isnull=True) & models.Q(scope='tenant'),
                name='organization_role_unique_tenant_name',
            ),
            models.UniqueConstraint(
                fields=['provider', 'name'],
                condition=models.Q(deleted_at__isnull=True) & models.Q(scope='provider'),
                name='organization_role_unique_provider_name',
            ),
            # Exactly one of (tenant, provider) is set, matching ``scope``.
            models.CheckConstraint(
                check=(
                    (models.Q(scope='tenant') & models.Q(tenant__isnull=False) & models.Q(provider__isnull=True))
                    | (models.Q(scope='provider') & models.Q(provider__isnull=False) & models.Q(tenant__isnull=True))
                ),
                name='organization_role_scope_consistency',
            ),
        ]

    def __str__(self):
        if self.scope == self.SCOPE_PROVIDER and self.provider_id:
            return f"{self.name} ({self.provider.name} provider)"
        if self.tenant_id:
            return f"{self.name} ({self.tenant.name})"
        return self.name

    def get_absolute_url(self):
        return reverse('organization:role_detail', kwargs={'pk': self.pk})

    @property
    def owner(self):
        """The Tenant or Provider that owns this role."""
        return self.tenant if self.scope == self.SCOPE_TENANT else self.provider

    # NOTE: Role intentionally has NO model-layer ``clean()`` validation of ``permissions``.
    # A global ``pre_save`` receiver (core/signals.py::validate_custom_validators_on_save) runs
    # ``clean()`` on every ChangeLoggingMixin save, so a hard check here would fire on ALL
    # writes — incompatible with this codebase's deliberate design:
    #   * RoleForm drops unknown codenames and only offers provider capabilities (manage_*)
    #     on provider-scoped roles;
    #   * the provider→tenant projection strips ``organization.manage_*`` via
    #     ``Membership.project_permissions_for_tenant`` (so a stray capability cannot
    #     project across tenants);
    #   * ``validate_role_permissions`` audits persisted stale codenames post-hoc — and the
    #     seed deliberately grants the full permission set to the tenant Administrator role.
    # Real write-time integrity belongs in a future ManyToManyField(auth.Permission) migration,
    # not a clean() that would break the seed and the audit command. See M6 in the RBAC review.

    def save(self, *args, **kwargs):
        # Auto-pick scope from the populated FK so callers can write either
        # ``Role.objects.create(tenant=...)`` or ``Role.objects.create(provider=...)`` without
        # repeating the implied ``scope=``. The CheckConstraint still enforces consistency:
        # if both FKs are set, scope follows ``provider`` (provider-scoped wins because the
        # tenant FK is then a mistake, caught by the constraint).
        if self.provider_id and not self.tenant_id:
            self.scope = self.SCOPE_PROVIDER
        elif self.tenant_id and not self.provider_id:
            self.scope = self.SCOPE_TENANT
        super().save(*args, **kwargs)


class Membership(ChangeLoggingMixin, models.Model):
    """A user's binding to either a Tenant or a Provider.

    The container FK pair is the *sole* discriminator of the two user kinds an admin
    thinks about — there is no separate ``person_type`` column:

      - **Provider staff** (MSP technician) — a ``provider``-scoped membership
        (``provider`` set, ``tenant`` null); ``is_provider_staff`` / ``kind == 'staff'``.
      - **Tenant member** — a ``tenant``-scoped membership (``tenant`` set, ``provider``
        null); ``kind == 'member'``.

    Exactly one of ``tenant`` / ``provider`` is set (enforced by a CheckConstraint).
    Login capability is a property of the ``User`` (``can_login``), not of the Membership.

    For provider-staff memberships, ``tenant_scope`` decides which of the provider's
    tenants the staff member reaches: ``explicit`` → ``assigned_tenants`` only,
    ``tenant_group`` → ``scope_group`` and descendants, ``all`` → every provider tenant.
    """
    # Provider memberships have no tenant of their own; tenant memberships do.
    # ChangeLoggingMixin must accept tenant=None entries for the former.
    changelog_global = True

    # "staff vs member" is derived from the FK pair (see ``kind``); these constants only
    # label the two kinds for display/report code, not a stored discriminator.
    KIND_STAFF = 'staff'
    KIND_MEMBER = 'member'
    KIND_CHOICES = [
        (KIND_MEMBER, _('Tenant member')),
        (KIND_STAFF, _('Provider staff (technician)')),
    ]

    SCOPE_EXPLICIT = 'explicit'
    SCOPE_TENANT_GROUP = 'tenant_group'
    SCOPE_ALL = 'all'
    SCOPE_CHOICES = [
        (SCOPE_EXPLICIT, _('Specific tenants')),
        (SCOPE_TENANT_GROUP, _('A tenant group + its descendants')),
        (SCOPE_ALL, _("All of the provider's tenants")),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name=_("User"),
    )
    # Exactly one of these is set (CheckConstraint); the populated FK *is* the kind.
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        related_name='memberships',
        blank=True, null=True,
        db_index=True,
        verbose_name=_("Tenant"),
    )
    provider = models.ForeignKey(
        'organization.Provider',
        on_delete=models.CASCADE,
        related_name='memberships',
        blank=True, null=True,
        db_index=True,
        verbose_name=_("Provider"),
    )
    roles = models.ManyToManyField(
        'organization.Role',
        related_name='memberships',
        blank=True,
        verbose_name=_("Roles"),
    )
    direct_permissions = models.JSONField(
        default=list, blank=True,
        verbose_name=_("Direct permissions"),
        help_text=_("Permission codenames granted directly to this membership, "
                    "independent of any role. Additive with role permissions."),
    )
    # Provider-staff tenant scoping (null for tenant memberships).
    tenant_scope = models.CharField(
        max_length=20,
        choices=SCOPE_CHOICES,
        blank=True, null=True,
        verbose_name=_("Tenant scope"),
        help_text=_("Provider staff only. How this technician reaches customer tenants."),
    )
    scope_group = models.ForeignKey(
        'organization.TenantGroup',
        on_delete=models.SET_NULL,
        related_name='provider_membership_scopes',
        blank=True, null=True,
        verbose_name=_("Scope group"),
        help_text=_("Used when ``tenant_scope='tenant_group'``."),
    )
    assigned_tenants = models.ManyToManyField(
        'organization.Tenant',
        related_name='provider_assignments',
        blank=True,
        verbose_name=_("Assigned tenants"),
        help_text=_("Used when ``tenant_scope='explicit'``."),
    )
    # Per-container activation. False = the user is suspended in THIS tenant/provider
    # (e.g. SCIM ``active=false``) but the row, roles, and any AssetHolder linkage are
    # retained for re-activation. Access gates treat an inactive membership as "not a
    # member".
    #
    # NOTE: the global ``User.is_active`` / ``User.can_login`` is synced from memberships
    # ONLY by the SCIM provisioning paths (``users/api/scim/views.py`` and
    # ``users/api/scim/provider_views.py``), which clear it when a provisioned user has no
    # active membership left. The interactive UI does NOT auto-clear it: a user whose last
    # membership is deactivated by an admin stays authenticated (and keeps any API tokens),
    # but on login lands on a "no accessible workspace" page (see
    # ``organization/views/dashboard`` + ``templates/registration/no_workspace.html``)
    # rather than a broken, permission-less dashboard.
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Active"))
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user']
        verbose_name = _("Membership")
        verbose_name_plural = _("Memberships")
        constraints = [
            # Exactly one container: provider (⇒ staff) XOR tenant (⇒ member).
            models.CheckConstraint(
                check=(
                    (models.Q(tenant__isnull=False) & models.Q(provider__isnull=True))
                    | (models.Q(tenant__isnull=True) & models.Q(provider__isnull=False))
                ),
                name='organization_membership_exactly_one_container',
            ),
            models.UniqueConstraint(
                fields=['user', 'tenant'],
                condition=models.Q(tenant__isnull=False),
                name='organization_membership_unique_user_tenant',
            ),
            models.UniqueConstraint(
                fields=['user', 'provider'],
                condition=models.Q(provider__isnull=False),
                name='organization_membership_unique_user_provider',
            ),
        ]

    def __str__(self):
        if self.provider_id:
            return f"{self.user} @ {self.provider} (provider staff)"
        if self.tenant_id:
            return f"{self.user} @ {self.tenant} ({self.get_kind_display()})"
        return f"{self.user} (unbound membership)"

    def get_absolute_url(self):
        return reverse('organization:membership_detail', kwargs={'pk': self.pk})

    @property
    def container(self):
        """The Tenant or Provider this membership belongs to."""
        return self.provider if self.provider_id else self.tenant

    @property
    def is_provider_staff(self):
        """A membership bound to a Provider is, by definition, provider staff."""
        return bool(self.provider_id)

    @property
    def kind(self):
        """``'staff'`` if bound to a provider, else ``'member'`` — derived from the FK."""
        return self.KIND_STAFF if self.provider_id else self.KIND_MEMBER

    def get_kind_display(self):
        """Human label for ``kind`` (mirrors Django's ``get_<field>_display`` ergonomics)."""
        return dict(self.KIND_CHOICES)[self.kind]

    def save(self, *args, **kwargs):
        # A provider-staff membership always needs a tenant_scope; default it on first save
        # when the caller bound a provider but left the scope blank. Provider/tenant XOR is
        # enforced by the CheckConstraint, so no discriminator field needs deriving here.
        if self.provider_id and not self.tenant_id and not self.tenant_scope:
            self.tenant_scope = self.SCOPE_EXPLICIT
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------ RBAC projection
    # These three methods are the CANONICAL source of truth for provider-staff tenant
    # reachability and for the provider→tenant permission projection. Every other place
    # that used to re-implement the ``tenant_scope`` branching or the ``manage_*`` strip
    # (``core.auth.MembershipBackend``, ``organization.access``, ``organization.signals``)
    # now delegates here — do NOT hand-copy this logic elsewhere.
    #
    # Provider-level capabilities (``organization.manage_*``) never grant inside a tenant,
    # so they are stripped whenever provider-scoped role permissions project into tenant
    # context. This prefix is the invariant those methods enforce.
    MANAGE_CAPABILITY_PREFIX = 'organization.manage_'

    def covers_tenant(self, tenant):
        """Whether ``tenant`` falls within this provider-staff membership's tenant scope.

        Provider-staff only (a tenant membership never "covers" other tenants).
        ``all`` → any tenant of the provider; ``tenant_group`` → the tenant's group is the
        scope group or a descendant; ``explicit`` (default) → tenant in ``assigned_tenants``.
        """
        if not self.provider_id:
            return False
        # A staff membership only ever covers tenants of its OWN provider. Without this guard
        # SCOPE_ALL / SCOPE_TENANT_GROUP would answer True for a foreign provider's tenant that
        # merely shares a group id — a cross-provider leak. Keeps this canonical helper in step
        # with scoped_tenant_ids (which filters provider_id in every branch).
        if tenant.provider_id != self.provider_id:
            return False
        scope = self.tenant_scope or self.SCOPE_EXPLICIT
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
        """The set of tenant ids this provider-staff membership can reach.

        Set-based counterpart to :meth:`covers_tenant` — used where callers need the whole
        reachable set at once (e.g. the tenant switcher) rather than testing one tenant.
        Returns an empty set for tenant memberships or when the scope resolves to nothing.
        """
        if not self.provider_id:
            return set()
        # inline import: avoid an organization.models <-> organization.access cycle at load
        from organization.models import Tenant

        scope = self.tenant_scope or self.SCOPE_EXPLICIT
        if scope == self.SCOPE_ALL:
            return set(
                Tenant._base_manager.filter(provider_id=self.provider_id)
                .values_list('pk', flat=True)
            )
        if scope == self.SCOPE_TENANT_GROUP:
            if not self.scope_group_id:
                return set()
            from organization.access import get_descendant_tenant_group_ids
            group_ids = get_descendant_tenant_group_ids(self.scope_group_id)
            return set(
                Tenant._base_manager.filter(
                    provider_id=self.provider_id, group_id__in=group_ids,
                ).values_list('pk', flat=True)
            )
        return set(
            self.assigned_tenants.filter(provider_id=self.provider_id)
            .values_list('pk', flat=True)
        )

    @classmethod
    def project_permissions_for_tenant(cls, perm_iterable):
        """Project provider-scoped role permissions into tenant context.

        Drops ``organization.manage_*`` capabilities (they are provider-level and never
        grant inside a tenant) and returns the remaining codenames as a list. ``None``
        entries in the iterable are treated as empty.
        """
        return [
            p for p in (perm_iterable or [])
            if not p.startswith(cls.MANAGE_CAPABILITY_PREFIX)
        ]


class TenantInvitation(ChangeLoggingMixin, models.Model):
    objects = TenantScopingManager()
    # Audit invite issuance/acceptance/deletion (security-relevant access grant)
    # but keep the one-time bearer token out of the changelog JSON.
    _change_logging_excluded_fields = ['updated_at', 'token']

    email = models.EmailField(verbose_name=_("Email"))
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.CASCADE, verbose_name=_("Tenant"))
    role = models.ForeignKey(
        'organization.Role',
        on_delete=models.CASCADE,
        related_name='invitations',
        verbose_name=_("Role")
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name=_("Invited By"))
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(verbose_name=_("Expires At"))
    accepted_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Accepted At"))

    @property
    def is_valid(self):
        return self.accepted_at is None and self.expires_at > timezone.now()

    class Meta:
        verbose_name = _("Tenant Invitation")
        verbose_name_plural = _("Tenant Invitations")

    def __str__(self):
        return f"Invite for {self.email} to {self.tenant.name}"


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


@transaction.atomic
def accept_invitation(invitation, user):
    from django.core.exceptions import ValidationError
    if not invitation.is_valid:
        raise ValidationError(_("This invitation has expired or has already been accepted."))

    # Accept-time escalation re-check (defence in depth): the invite form already guards the
    # role grant at issuance, but roles/permissions can change between issue and accept, and the
    # inviter may have lost privileges since. Re-validate the role's permissions against the
    # inviter's CURRENT effective perms in the tenant; reject if they can no longer grant it.
    # inline import: core.auth.guards -> core.auth -> organization would cycle at module load.
    from core.auth.guards import validate_permission_grant
    if invitation.role is not None:
        # Fail CLOSED when the inviter no longer exists (invited_by is SET_NULL): total
        # privilege loss is the strongest case this re-check must catch, but
        # validate_permission_grant treats a None granting user as a trusted no-op, so guard
        # it explicitly rather than letting the grant through unverified.
        if invitation.invited_by is None:
            raise ValidationError(_(
                "This invitation can no longer be accepted because the person who issued "
                "it no longer has an account. Please request a new invitation."
            ))
        validate_permission_grant(
            invitation.invited_by, invitation.role.permissions or [], invitation.tenant,
        )

    # 1. Create the tenant membership (a tenant member — provider is null)
    membership = Membership.objects.create(
        user=user,
        tenant=invitation.tenant,
    )
    membership.roles.add(invitation.role)

    # 2. Mark Invitation as accepted
    invitation.accepted_at = timezone.now()
    invitation.save()

    # 3. Match and bind the User account to an existing AssetHolder record if present
    holder = AssetHolder.objects.filter(
        tenant=invitation.tenant,
        email__iexact=invitation.email,
        user__isnull=True
    ).first()

    if holder:
        holder.user = user
        holder.save()


# --------------------------------------------------------------------------- Provider (MSP)
# The Provider layer sits ABOVE tenants: one managing organization (MSP) administers its own
# IT and its customers' IT from a single install. Single-company installs simply have no
# Provider rows, so the whole layer stays invisible.
#
# Provider-level capabilities (manage_tenants, manage_staff, manage_groups, manage_provider)
# are plain Django permissions declared on Provider.Meta.permissions. They are granted by
# attaching them to a provider-scoped Role; the auth backend resolves them through normal
# ``user.has_perm()`` calls — there is no second authorization system.

class Provider(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    """An MSP / managing organization that administers one or more customer Tenants."""
    changelog_global = True  # above tenants → changelog attributed to tenant=None
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    name = models.CharField(max_length=100, verbose_name=_("Name"))
    slug = models.SlugField(max_length=100, verbose_name=_("Slug"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    comments = models.TextField(blank=True, verbose_name=_("Comments"))
    internal_tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.SET_NULL,
        related_name='provider_internal',
        blank=True,
        null=True,
        verbose_name=_("Internal tenant"),
        help_text=_("The provider's own IT inventory tenant ('home base' for provider staff)."),
    )
    settings = models.JSONField(default=dict, blank=True, verbose_name=_("Settings"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Provider")
        verbose_name_plural = _("Providers")
        permissions = [
            # Provider-level capabilities. Held via a provider-scoped Role attached to a
            # provider-staff Membership. Gated with ``user.has_perm('organization.<cap>')``.
            ('manage_provider', 'Can manage provider settings'),
            ('manage_tenants', 'Can manage customer tenants under a provider'),
            ('manage_staff', 'Can manage provider staff'),
            ('manage_groups', 'Can manage user groups'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='organization_provider_unique_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='organization_provider_unique_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:provider_detail', kwargs={'pk': self.pk})


