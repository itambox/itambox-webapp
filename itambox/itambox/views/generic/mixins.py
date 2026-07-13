from django.apps import apps
from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse, NoReverseMatch
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import View

from itambox.utils import get_model_viewname


SUPERUSER_ONLY_MUTATION_MODELS = frozenset({
    'organization.tenantgroup',
})


def user_can_mutate_model(user, model):
    """Apply model-wide mutation policies shared by every generic write path."""
    if model is None:
        return True
    return (
        model._meta.label_lower not in SUPERUSER_ONLY_MUTATION_MODELS
        or bool(user and user.is_superuser)
    )


class CachedObjectMixin:
    """Cache ``get_object()`` for the lifetime of the request.

    CBVs call ``get_object()`` from ``has_permission``, ``get()``,
    ``get_template_names`` and ``get_context_data``; without caching, every call
    re-runs the detail query — including its full select_related/prefetch_related
    graph — and the prefetches on one copy are invisible to the others.
    """

    def get_object(self, *args, **kwargs):
        if getattr(self, '_cached_object', None) is None:
            self._cached_object = super().get_object(*args, **kwargs)
        return self._cached_object


class ObjectPermissionRequiredMixin(AccessMixin):
    additional_permissions = list()

    def get_required_permission(self):
        raise NotImplementedError

    def has_permission(self):
        user = self.request.user
        permission_required = self.get_required_permission()
        if user.has_perms((permission_required, *self.additional_permissions)):
            return True
        return False

    def dispatch(self, request, *args, **kwargs):
        if not self.has_permission():
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class GetReturnURLMixin:
    default_return_url = None

    def get_return_url(self, request, obj=None):
        return_url = request.GET.get('return_url') or request.POST.get('return_url')
        if return_url and url_has_allowed_host_and_scheme(return_url, allowed_hosts=request.get_host(), require_https=request.is_secure()):
            return return_url
        if obj is not None and obj.pk and hasattr(obj, 'get_absolute_url'):
            return obj.get_absolute_url()
        if self.default_return_url is not None:
            return reverse(self.default_return_url)
        if hasattr(self, 'queryset'):
            try:
                return reverse(get_model_viewname(self.queryset.model, 'list'))
            except NoReverseMatch:
                pass
        return reverse('dashboard')


class ActionsMixin:
    actions = ()

    def get_permitted_actions(self, user, model=None):
        model = model or getattr(self, 'model', None)
        if model is None and hasattr(self, 'queryset'):
            model = self.queryset.model
        permitted = []
        for action in self.actions:
            required_perms = [
                f'{model._meta.app_label}.{perm}_{model._meta.model_name}'
                for perm in action.permissions_required
            ]
            if not required_perms or user.has_perms(required_perms):
                permitted.append(action)
        return permitted


class TableMixin:

    def get_table(self, data, request, bulk_actions=True):
        table = self.table(data)
        if bulk_actions and 'pk' in table.base_columns:
            table.columns.show('pk')
        table.configure(request)
        return table


class TenantScopingViewMixin:
    """Apply ``filter_by_tenant()`` to any queryset that supports it.

    Placed in the MRO before concrete view base classes so that both
    ``get_queryset`` in the framework and any per-view override automatically
    respect the active tenant context.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        if hasattr(queryset, 'filter_by_tenant'):
            queryset = queryset.filter_by_tenant()
        return queryset


def filter_permitted_rows(user, rows, model, action):
    """Split ``rows`` into ``(permitted, skipped_count)`` by the per-object perm.

    Bulk views dispatch on an AMBIENT permission check: under a tenant-group
    scope it passes when the permission is held in ANY accessible tenant of the
    subtree, while the scoped queryset spans EVERY accessible tenant there —
    reach alone (not permission content) decides queryset membership. Re-check
    ``<action>_<model>`` per row (anchored at the row's own tenant) so a member
    cannot mutate rows of a sibling tenant where their role conveys no such
    permission. Rows without a resolvable tenant (global/shared) fall back to
    the ambient gate that already passed. Cheap: the backend caches effective
    permissions per (user, tenant), so the cost is one resolution per distinct
    tenant, not per row.
    """
    perm = f'{model._meta.app_label}.{action}_{model._meta.model_name}'
    permitted, skipped = [], 0
    for obj in rows:
        if user.has_perm(perm, obj=obj):
            permitted.append(obj)
        else:
            skipped += 1
    return permitted, skipped


class BulkViewMixin:
    """Shared helpers for ``ObjectBulkEditView`` and ``ObjectBulkDeleteView``.

    Both bulk views need to resolve the target model from several possible
    sources (view attribute, form class, or a ``model_name`` request param)
    and then obtain a queryset scoped to the current tenant and a list of PKs.
    """

    def _get_model(self):
        if getattr(self, 'queryset', None) is not None:
            return self.queryset.model
        if hasattr(self, 'model') and self.model:
            return self.model
        if getattr(self, 'form_class', None) and hasattr(self.form_class, '_meta'):
            return self.form_class._meta.model
        if hasattr(self, 'request') and self.request:
            model_name = self.request.POST.get('model_name') or self.request.GET.get('model_name')
            if model_name:
                try:
                    app_label, mn = model_name.split('.')
                    model = apps.get_model(app_label, mn)
                except (ValueError, LookupError):
                    raise Http404
                # A swapped-out model (e.g. the default ``auth.User`` once AUTH_USER_MODEL is
                # overridden) has no usable manager — reject it like any non-tenant model.
                # ``_meta.swapped`` is checked first so the short-circuit never touches the
                # unavailable ``.objects`` manager.
                if model._meta.swapped or not hasattr(model.objects, 'filter_by_tenant'):
                    raise Http404
                return model
        return None

    def _get_queryset(self, pks):
        qs = self.queryset if getattr(self, 'queryset', None) is not None else self._get_model().objects.all()
        if hasattr(qs, 'filter_by_tenant'):
            qs = qs.filter_by_tenant()
        return qs.filter(pk__in=pks)
