from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import get_object_or_404
from django.views.generic import View


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
        from django.urls import reverse, NoReverseMatch
        from itambox.utils import get_model_viewname

        return_url = request.GET.get('return_url') or request.POST.get('return_url')
        if return_url:
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
