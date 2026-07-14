import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from itambox.utils import get_model_viewname
from itambox.views.generic.mixins import filter_permitted_rows
from itambox.views.generic.utils import safe_return_url

logger = logging.getLogger(__name__)


class HtmxActionMixin:
    """Emit a 204 + HX-Trigger response for HTMX callers; fall back to a
    redirect for plain-HTTP callers.

    Subclasses implement ``perform_action(request)`` which returns a success
    message string and, optionally, sets ``self.list_url`` for the redirect.
    """

    def _htmx_or_redirect(self, request, success_msg, list_url):
        if request.headers.get('HX-Request') or getattr(request, 'htmx', False):
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "tableRefreshRequired": None,
                "showMessage": {
                    "message": success_msg,
                    "level": "success",
                },
            })
            return response

        messages.success(request, success_msg)
        return HttpResponseRedirect(
            safe_return_url(request, request.META.get('HTTP_REFERER'), list_url)
        )


class ObjectRestoreView(HtmxActionMixin, PermissionRequiredMixin, LoginRequiredMixin, View):
    def has_permission(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
        self.model = self.content_type.model_class()
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name

        manager = getattr(self.model, 'all_objects', self.model._base_manager)
        self.object = get_object_or_404(manager, pk=self.kwargs['object_id'])

        if not self.request.user.is_superuser and not self.request.user.has_perm('core.change_recyclebin'):
            return False

        return self.request.user.has_perm(f'{app_label}.change_{model_name}', self.object)

    def post(self, request, *args, **kwargs):
        self.object.restore()

        success_msg = _("Restored {model} {object}").format(
            model=self.model._meta.verbose_name,
            object=self.object,
        )

        try:
            list_url = reverse(get_model_viewname(self.model, 'list')) + "?deleted=true"
        except Exception:
            list_url = '/'

        return self._htmx_or_redirect(request, success_msg, list_url)


class ObjectPurgeView(HtmxActionMixin, PermissionRequiredMixin, LoginRequiredMixin, View):
    def has_permission(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
        self.model = self.content_type.model_class()
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name

        manager = getattr(self.model, 'all_objects', self.model._base_manager)
        self.object = get_object_or_404(manager, pk=self.kwargs['object_id'])

        if not self.request.user.is_superuser and not self.request.user.has_perm('core.delete_recyclebin'):
            return False

        return self.request.user.has_perm(f'{app_label}.delete_{model_name}', self.object)

    def post(self, request, *args, **kwargs):
        obj_repr = str(self.object)
        self.object.delete(force_hard_delete=True)

        success_msg = _("Permanently purged {model} {object}").format(
            model=self.model._meta.verbose_name,
            object=obj_repr,
        )

        try:
            list_url = reverse(get_model_viewname(self.model, 'list')) + "?deleted=true"
        except Exception:
            list_url = '/'

        return self._htmx_or_redirect(request, success_msg, list_url)


class ObjectBulkRestoreView(HtmxActionMixin, PermissionRequiredMixin, LoginRequiredMixin, View):
    def has_permission(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
        self.model = self.content_type.model_class()
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name

        if not self.request.user.is_superuser and not self.request.user.has_perm('core.change_recyclebin'):
            return False

        return self.request.user.has_perm(f'{app_label}.change_{model_name}')

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist('pk')
        if not pks:
            messages.warning(request, _("No items selected."))
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

        manager = getattr(self.model, 'all_objects', self.model._base_manager)
        queryset = manager.filter(pk__in=pks, deleted_at__isnull=False)

        # Per-row change-perm enforcement (see filter_permitted_rows): the
        # dispatch gate alone is too coarse inside a multi-tenant group scope.
        rows, skipped = filter_permitted_rows(request.user, queryset, self.model, 'change')
        if skipped:
            messages.warning(request, _(
                "Skipped %(count)s %(objects)s you do not have permission to change."
            ) % {'count': skipped, 'objects': self.model._meta.verbose_name_plural})

        count = 0
        with transaction.atomic():
            for obj in rows:
                obj.restore()
                count += 1

        success_msg = _("Successfully restored {count} {model_plural}.").format(
            count=count,
            model_plural=self.model._meta.verbose_name_plural,
        )

        try:
            list_url = reverse(get_model_viewname(self.model, 'list')) + "?deleted=true"
        except Exception:
            list_url = '/'

        return self._htmx_or_redirect(request, success_msg, list_url)


class ObjectBulkPurgeView(HtmxActionMixin, PermissionRequiredMixin, LoginRequiredMixin, View):
    def has_permission(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
        self.model = self.content_type.model_class()
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name

        if not self.request.user.is_superuser and not self.request.user.has_perm('core.delete_recyclebin'):
            return False

        return self.request.user.has_perm(f'{app_label}.delete_{model_name}')

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist('pk')
        if not pks:
            messages.warning(request, _("No items selected."))
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

        manager = getattr(self.model, 'all_objects', self.model._base_manager)
        queryset = manager.filter(pk__in=pks, deleted_at__isnull=False)

        # Per-row delete-perm enforcement (see filter_permitted_rows): the
        # dispatch gate alone is too coarse inside a multi-tenant group scope.
        rows, skipped = filter_permitted_rows(request.user, queryset, self.model, 'delete')
        if skipped:
            messages.warning(request, _(
                "Skipped %(count)s %(objects)s you do not have permission to delete."
            ) % {'count': skipped, 'objects': self.model._meta.verbose_name_plural})

        count = 0
        with transaction.atomic():
            for obj in rows:
                obj.delete(force_hard_delete=True)
                count += 1

        success_msg = _("Successfully permanently purged {count} {model_plural}.").format(
            count=count,
            model_plural=self.model._meta.verbose_name_plural,
        )

        try:
            list_url = reverse(get_model_viewname(self.model, 'list')) + "?deleted=true"
        except Exception:
            list_url = '/'

        return self._htmx_or_redirect(request, success_msg, list_url)
