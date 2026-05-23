import logging
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.text import slugify

logger = logging.getLogger(__name__)

MAX_PAGINATION_LIMIT = 200

def paginate_queryset(qs, limit=None, offset=None, max_limit=MAX_PAGINATION_LIMIT):
    offset = max(offset or 0, 0)
    limit = max(min(limit or max_limit, max_limit), 0)
    if offset > 0:
        qs = qs[offset:]
    return qs[:limit]

def check_permission(info, perm, obj=None):
    user = info.context.user
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication credentials were not provided.")
    if not user.has_perm(perm, obj=obj):
        raise PermissionDenied("Permission denied.")
    return user

def get_object_or_denied(model, pk, user, tenant=None):
    try:
        qs = model.objects.all()
        if tenant and hasattr(model, 'tenant'):
            qs = qs.filter(tenant=tenant)
        obj = qs.get(pk=pk)
        return obj
    except model.DoesNotExist:
        raise PermissionDenied("Object not found or access denied.")

def generate_slug(instance):
    logger.debug("GENERATE SLUG called for %s with current slug: %r", instance.__class__.__name__, getattr(instance, 'slug', None))
    if not getattr(instance, 'slug', None):
        slug_src = ""
        source = getattr(instance, 'slug_source', 'name')
        if isinstance(source, (list, tuple)):
            source_values = []
            for field_name in source:
                if '__' in field_name:
                    parts = field_name.split('__')
                    obj = instance
                    for part in parts:
                        obj = getattr(obj, part, None) if obj else None
                    val = str(obj) if obj else ""
                else:
                    val = getattr(instance, field_name, "")
                if val:
                    source_values.append(str(val))
            slug_src = "-".join(source_values)
        else:
            slug_src = getattr(instance, source, "")
        
        instance.slug = slugify(slug_src) or "auto-slug"
        
        base_slug = instance.slug
        counter = 1
        model_class = instance.__class__
        manager = getattr(model_class, '_base_manager', model_class.objects)
        
        while manager.filter(slug=instance.slug).exclude(pk=instance.pk).exists():
            instance.slug = f"{base_slug}-{counter}"
            counter += 1

