import logging
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

MAX_PAGINATION_LIMIT = 200

def paginate_queryset(qs, limit=None, offset=None, max_limit=MAX_PAGINATION_LIMIT):
    """
    Paginate a queryset safely, clamping offset and limit to prevent negative slices
    or excessively large limits.
    """
    if offset is not None and offset < 0:
        raise ValueError("Offset cannot be negative.")
    if limit is not None and limit < 0:
        raise ValueError("Limit cannot be negative.")
        
    offset = max(offset or 0, 0)
    limit = max(min(limit or max_limit, max_limit), 0)
    return qs[offset:offset + limit]

def check_permission(info, perm, obj=None):
    user = info.context.user
    if not user or not user.is_authenticated:
        raise PermissionDenied(_("Authentication credentials were not provided."))
    if not user.has_perm(perm, obj=obj):
        raise PermissionDenied(_("Permission denied."))
    return user

def get_object_or_denied(model, pk, user, tenant=None):
    try:
        qs = model.objects.all()
        if tenant and hasattr(model, 'tenant'):
            qs = qs.filter(tenant=tenant)
        obj = qs.get(pk=pk)
        return obj
    except model.DoesNotExist:
        raise PermissionDenied(_("Permission denied."))

def generate_slug(instance):
    logger.debug("GENERATE SLUG called for %s with current slug: %r", instance.__class__.__name__, getattr(instance, 'slug', None))
    if not getattr(instance, 'slug', None):
        from itambox.utils import generate_unique_slug
        generate_unique_slug(instance, getattr(instance, 'slug_source', 'name'))

