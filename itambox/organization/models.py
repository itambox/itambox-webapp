from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.text import slugify # For potential slug generation
from django.urls import reverse
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings # Import settings
from core.models import BaseModel, ChangeLoggingMixin, StandardModel, VaultModel
from core.managers import TenantScopingManager
from core.mixins import ExportableMixin, TaggableMixin, JournalingMixin, AutoSlugMixin, CloneableMixin, ImageAttachmentMixin, FileAttachmentMixin, BookmarkableMixin, SubscribableMixin

# Create your models here.

class Location(SubscribableMixin, StandardModel):
    objects = TenantScopingManager()
    STATUS_PLANNED = 'planned'
    STATUS_STAGING = 'staging'
    STATUS_ACTIVE = 'active'
    STATUS_DECOMMISSIONING = 'decommissioning'
    STATUS_RETIRED = 'retired'
    STATUS_CHOICES = [
        (STATUS_PLANNED, 'Planned'),
        (STATUS_STAGING, 'Staging'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_DECOMMISSIONING, 'Decommissioning'),
        (STATUS_RETIRED, 'Retired'),
    ]

    site = models.ForeignKey(
        'Site', # Use string reference
        on_delete=models.CASCADE, # Or PROTECT if locations shouldn't be deleted when site is
        related_name='locations',
        db_index=True
        # null=True # REMOVED temporary null
        # No blank=True as per requirements
    )
    name = models.CharField(max_length=100, db_index=True) # Changed max_length based on Site/Region etc.
    slug = models.SlugField(max_length=100, unique=True)
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='children',
        blank=True,
        null=True
    )
    tenant = models.ForeignKey(
        'Tenant',
        on_delete=models.SET_NULL, # Or PROTECT
        related_name='locations',
        blank=True,
        null=True,
        db_index=True
    )
    facility = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True) # Using TextField for potentially longer descriptions
    tags = models.ManyToManyField('extras.Tag', related_name="locations", blank=True)

    class Meta:
        ordering = ['site', 'name']
        # Ensure unique combination of site and name/slug?
        # constraints = [
        #     models.UniqueConstraint(fields=['site', 'name'], name='unique_location_name_per_site'),
        #     models.UniqueConstraint(fields=['site', 'slug'], name='unique_location_slug_per_site'),
        # ]
        # Decided against constraints for now, slug is already unique globally.
        # Can add site-specific constraints later if needed.
        verbose_name = _("Location")
        verbose_name_plural = _("Locations")


    def __str__(self):
        # Consider showing parent hierarchy later if needed
        return self.name

    def get_absolute_url(self):
        return reverse('organization:location_detail', kwargs={'pk': self.pk})

class Region(StandardModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='children',
        blank=True,
        null=True,
        db_index=True
    )
    description = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="regions", blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Region")
        verbose_name_plural = _("Regions")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:region_detail', kwargs={'pk': self.pk})

class SiteGroup(StandardModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='children',
        blank=True,
        null=True
    )
    description = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="sitegroups", blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Site Group")
        verbose_name_plural = _("Site Groups")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:sitegroup_detail', kwargs={'pk': self.pk})

class TenantGroup(StandardModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='children',
        blank=True,
        null=True
    )
    description = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="tenantgroups", blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Tenant Group")
        verbose_name_plural = _("Tenant Groups")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:tenantgroup_detail', kwargs={'pk': self.pk})

class Tenant(VaultModel, BookmarkableMixin):
    objects = TenantScopingManager()
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    group = models.ForeignKey(
        TenantGroup, # Reference the new TenantGroup model
        on_delete=models.SET_NULL, # Or PROTECT
        related_name='tenants',
        blank=True,
        null=True
    )
    description = models.TextField(blank=True)
    comments = models.TextField(blank=True) # Added comments
    tags = models.ManyToManyField('extras.Tag', related_name="tenants", blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Tenant")
        verbose_name_plural = _("Tenants")

    def get_absolute_url(self):
        return reverse('organization:tenant_detail', kwargs={'pk': self.pk})

    def __str__(self):
        return self.name

class Site(VaultModel, BookmarkableMixin):
    objects = TenantScopingManager()
    STATUS_PLANNED = 'planned'
    STATUS_STAGING = 'staging'
    STATUS_ACTIVE = 'active'
    STATUS_DECOMMISSIONING = 'decommissioning'
    STATUS_RETIRED = 'retired'
    STATUS_CHOICES = [
        (STATUS_PLANNED, 'Planned'),
        (STATUS_STAGING, 'Staging'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_DECOMMISSIONING, 'Decommissioning'),
        (STATUS_RETIRED, 'Retired'),
    ]

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    # Use local models for FKs within the app
    region = models.ForeignKey(Region, on_delete=models.SET_NULL, related_name='sites', blank=True, null=True, db_index=True)
    group = models.ForeignKey(SiteGroup, on_delete=models.SET_NULL, related_name='sites', blank=True, null=True, db_index=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, related_name='sites', blank=True, null=True, db_index=True)
    facility = models.CharField(max_length=100, blank=True)
    time_zone = models.CharField(max_length=63, blank=True) # Consider using timezone_field package later
    description = models.CharField(max_length=200, blank=True)
    physical_address = models.CharField(max_length=200, blank=True)
    shipping_address = models.CharField(max_length=200, blank=True)
    latitude = models.DecimalField(max_digits=8, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    comments = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="sites", blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Site")
        verbose_name_plural = _("Sites")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:site_detail', kwargs={'pk': self.pk})

# +++ AssetHolder Model +++
class AssetHolder(SubscribableMixin, StandardModel):
    objects = TenantScopingManager()
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, # Keep holder if user is deleted, set user link to null
        related_name='asset_holder_profile', # Custom related_name
        blank=True,
        null=True
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    upn = models.CharField(max_length=255, verbose_name='User Principal Name', unique=True)
    email = models.EmailField(blank=True, null=True)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='asset_holders',
        db_index=True
    )
    description = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', blank=True, related_name='organization_assetholders') # M2M to extras.Tag

    class Meta:
        ordering = ['last_name', 'first_name', 'upn']
        constraints = [
            models.UniqueConstraint(fields=['upn'], name='organization_assetholder_unique_upn')
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

    @property
    def checked_out_asset_count(self):
        return self.checked_out_assets.count()

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.upn})"

    def get_absolute_url(self):
        # Assuming you'll have a detail view named 'assetholder_detail'
        return reverse('organization:assetholder_detail', kwargs={'pk': self.pk})



class ContactRole(AutoSlugMixin, StandardModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Contact Role")
        verbose_name_plural = _("Contact Roles")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:contactrole_detail', kwargs={'pk': self.pk})


class Contact(StandardModel):
    name = models.CharField(max_length=100)
    title = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    web_url = models.URLField(blank=True, verbose_name="Web URL")
    description = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', blank=True, related_name='organization_contacts')

    class Meta:
        ordering = ['name']
        verbose_name = _("Contact")
        verbose_name_plural = _("Contacts")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization:contact_detail', kwargs={'pk': self.pk})


class ContactAssignment(ChangeLoggingMixin, BaseModel):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='assignments')
    role = models.ForeignKey(ContactRole, on_delete=models.PROTECT, related_name='assignments')
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    assigned_object = GenericForeignKey('content_type', 'object_id')
    priority = models.CharField(
        max_length=50,
        choices=[
            ('primary', 'Primary'),
            ('secondary', 'Secondary'),
            ('tertiary', 'Tertiary'),
            ('inactive', 'Inactive'),
        ],
        blank=True,
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

    def __str__(self):
        return f"{self.contact} ({self.role}) assigned to {self.assigned_object}"


import uuid
from django.utils import timezone
from django.db import transaction

class TenantRole(StandardModel):
    objects = TenantScopingManager()

    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        related_name='custom_roles'
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    permissions = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = ('tenant', 'name')
        ordering = ['name']
        verbose_name = "Tenant Role"
        verbose_name_plural = "Tenant Roles"

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"


class TenantMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    role = models.ForeignKey(
        'organization.TenantRole',
        on_delete=models.PROTECT,
        related_name='memberships'
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'tenant')
        verbose_name = _("Tenant Membership")
        verbose_name_plural = _("Tenant Memberships")

    def __str__(self):
        return f"{self.user.username} is {self.role.name} at {self.tenant.name}"


class TenantInvitation(models.Model):
    email = models.EmailField()
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.CASCADE)
    role = models.ForeignKey(
        'organization.TenantRole',
        on_delete=models.CASCADE,
        related_name='invitations'
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_valid(self):
        return self.accepted_at is None and self.expires_at > timezone.now()

    class Meta:
        verbose_name = _("Tenant Invitation")
        verbose_name_plural = _("Tenant Invitations")

    def __str__(self):
        return f"Invite for {self.email} to {self.tenant.name}"


@transaction.atomic
def accept_invitation(invitation, user):
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


