from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from core.utils import log_change, serialize_object
from core.choices import ObjectChangeActionChoices
from .models import Site, Location, Region, SiteGroup, Tenant, TenantGroup, AssetHolder # Add remaining models

# --- Dictionaries to store prechange data --- #
_site_prechange_data = {}
_location_prechange_data = {}
_region_prechange_data = {}
_sitegroup_prechange_data = {}
_tenant_prechange_data = {}
_tenantgroup_prechange_data = {}
_assetholder_prechange_data = {}

# --- Signal Handlers for Site --- #

@receiver(pre_save, sender=Site)
def capture_site_prechange(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            _site_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist:
            _site_prechange_data[instance.pk] = None

@receiver(post_save, sender=Site)
def handle_site_saved(sender, instance, created, **kwargs):
    if created:
        log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _site_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange:
            log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=Site)
def handle_site_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance)
    log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange)

# --- Signal Handlers for Location --- #

@receiver(pre_save, sender=Location)
def capture_location_prechange(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            _location_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist:
            _location_prechange_data[instance.pk] = None

@receiver(post_save, sender=Location)
def handle_location_saved(sender, instance, created, **kwargs):
    if created:
        log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _location_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange:
            log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=Location)
def handle_location_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance)
    log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange)

# --- Signal Handlers for Region --- #

@receiver(pre_save, sender=Region)
def capture_region_prechange(sender, instance, **kwargs):
    if instance.pk:
        try: old = sender.objects.get(pk=instance.pk); _region_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist: _region_prechange_data[instance.pk] = None

@receiver(post_save, sender=Region)
def handle_region_saved(sender, instance, created, **kwargs):
    if created: log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _region_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange: log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=Region)
def handle_region_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance); log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange)

# --- Signal Handlers for SiteGroup --- #

@receiver(pre_save, sender=SiteGroup)
def capture_sitegroup_prechange(sender, instance, **kwargs):
    if instance.pk:
        try: old = sender.objects.get(pk=instance.pk); _sitegroup_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist: _sitegroup_prechange_data[instance.pk] = None

@receiver(post_save, sender=SiteGroup)
def handle_sitegroup_saved(sender, instance, created, **kwargs):
    if created: log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _sitegroup_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange: log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=SiteGroup)
def handle_sitegroup_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance); log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange)

# --- Signal Handlers for Tenant --- #

@receiver(pre_save, sender=Tenant)
def capture_tenant_prechange(sender, instance, **kwargs):
    if instance.pk:
        try: old = sender.objects.get(pk=instance.pk); _tenant_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist: _tenant_prechange_data[instance.pk] = None

@receiver(post_save, sender=Tenant)
def handle_tenant_saved(sender, instance, created, **kwargs):
    if created: log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _tenant_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange: log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=Tenant)
def handle_tenant_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance); log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange)

# --- Signal Handlers for TenantGroup --- #

@receiver(pre_save, sender=TenantGroup)
def capture_tenantgroup_prechange(sender, instance, **kwargs):
    if instance.pk:
        try: old = sender.objects.get(pk=instance.pk); _tenantgroup_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist: _tenantgroup_prechange_data[instance.pk] = None

@receiver(post_save, sender=TenantGroup)
def handle_tenantgroup_saved(sender, instance, created, **kwargs):
    if created: log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _tenantgroup_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange: log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=TenantGroup)
def handle_tenantgroup_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance); log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange)

# --- Signal Handlers for AssetHolder --- #

@receiver(pre_save, sender=AssetHolder)
def capture_assetholder_prechange(sender, instance, **kwargs):
    if instance.pk:
        try: old = sender.objects.get(pk=instance.pk); _assetholder_prechange_data[instance.pk] = serialize_object(old)
        except sender.DoesNotExist: _assetholder_prechange_data[instance.pk] = None

@receiver(post_save, sender=AssetHolder)
def handle_assetholder_saved(sender, instance, created, **kwargs):
    if created: log_change(instance, ObjectChangeActionChoices.ACTION_CREATE, postchange_data=serialize_object(instance))
    else:
        prechange = _assetholder_prechange_data.pop(instance.pk, None)
        postchange = serialize_object(instance)
        if prechange is not None and prechange != postchange: log_change(instance, ObjectChangeActionChoices.ACTION_UPDATE, prechange_data=prechange, postchange_data=postchange)

@receiver(post_delete, sender=AssetHolder)
def handle_assetholder_deleted(sender, instance, **kwargs):
    prechange = serialize_object(instance); log_change(instance, ObjectChangeActionChoices.ACTION_DELETE, prechange_data=prechange)

# --- Add handlers for Region, Tenant, etc. following the same pattern --- # 