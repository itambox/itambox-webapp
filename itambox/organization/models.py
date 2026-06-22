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
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
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
        # Contact is NOT tenant-scoped (no `tenant` field; SoftDeleteManager),
        # so the only tenant signal for an assignment is the generic-FK target.
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

class TenantRole(StandardModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        related_name='custom_roles',
        verbose_name=_("Tenant")
    )
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    permissions = models.JSONField(default=list, blank=True, verbose_name=_("Permissions"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Tenant Role")
        verbose_name_plural = _("Tenant Roles")
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                condition=models.Q(deleted_at__isnull=True),
                name='organization_tenantrole_unique_tenant_name'
            )
        ]

    def __str__(self):
        # tenant may be unset transiently (e.g. an unsaved clone awaiting a
        # tenant assignment), so guard the dereference.
        if self.tenant_id:
            return f"{self.name} ({self.tenant.name})"
        return self.name


class TenantMembership(ChangeLoggingMixin, models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name=_("User")
    )
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name=_("Tenant")
    )
    role = models.ForeignKey(
        'organization.TenantRole',
        on_delete=models.PROTECT,
        related_name='memberships',
        verbose_name=_("Role")
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    # Per-tenant activation. False = the user is suspended/deprovisioned in THIS tenant
    # (e.g. an IdP sent SCIM active=false) but the row, role and AssetHolder are retained
    # so the membership can be reactivated and other tenants are unaffected. Access gates
    # (TenantMembershipBackend, TenantMiddleware) must treat an inactive membership as
    # "not a member" for this tenant. The global User.is_active is only cleared when the
    # user has NO active membership in any tenant.
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Active"))

    class Meta:
        verbose_name = _("Tenant Membership")
        verbose_name_plural = _("Tenant Memberships")
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'tenant'],
                name='organization_tenantmembership_unique_user_tenant'
            )
        ]

    def __str__(self):
        return f"{self.user.username} is {self.role.name} at {self.tenant.name}"


class TenantInvitation(models.Model):
    objects = TenantScopingManager()

    email = models.EmailField(verbose_name=_("Email"))
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.CASCADE, verbose_name=_("Tenant"))
    role = models.ForeignKey(
        'organization.TenantRole',
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

    # 1. Create the Workspace Membership
    TenantMembership.objects.create(
        user=user,
        tenant=invitation.tenant,
        role=invitation.role
    )
    
    # 2. Mark Invitation as accepted
    invitation.accepted_at = timezone.now()
    invitation.save()
    
    # 3. Match and bind the User account to their existing physical AssetHolder record (if present)
    holder = AssetHolder.objects.filter(
        tenant=invitation.tenant,
        email__iexact=invitation.email,
        user__isnull=True
    ).first()
    
    if holder:
        holder.user = user
        holder.save()


