import json
import logging
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, View

from itambox.views.htmx import BaseHTMXView

logger = logging.getLogger(__name__)


class GenericTransactionView(PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, FormView):
    queryset = None
    model_form = None
    service_callable = None
    context_object_name = 'object'
    success_message = _("Operation completed successfully.")
    hx_trigger = "tableRefreshRequired"
    form_field_map = {}
    form_exclude_fields = ()
    #: When True, successful HTMX submissions answer with HX-Redirect to the
    #: object's detail page instead of 204 + closeModal/refresh triggers.
    hx_redirect_on_success = False
    #: django-template-partials reference ("template.html#partial-name") rendered
    #: on validation errors for HTMX requests. Returns only the form fragment with
    #: a 422 status, so the modal body is re-swapped without nesting the full modal.
    error_partial = None

    def get_permission_required(self):
        # Fail closed: a service/action view that mutates state must declare the
        # permission(s) it requires. A missing (None) permission_required is a
        # developer error, not an open door — historically it silently allowed
        # ANY authenticated tenant member to run the action (B3). Views that
        # intentionally perform their own per-object authorization (e.g. an
        # ownership check inside perform_action/form_valid) opt out explicitly by
        # setting `permission_required = ()`.
        if self.permission_required is None:
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} is missing permission_required. Set it "
                f"to the required permission(s), or to an empty tuple () to opt into "
                f"handling authorization itself."
            )
        if isinstance(self.permission_required, str):
            return (self.permission_required,)
        return self.permission_required

    def has_permission(self):
        perms = self.get_permission_required()
        if not perms:
            return True
        try:
            obj = self.get_object()
        except Http404:
            # 404 (not 403) for objects outside the tenant scope: don't reveal
            # whether the pk exists in another tenant. Anonymous users fall
            # through to the permission check (and the login redirect).
            if self.request.user.is_authenticated:
                raise
            obj = None
        return self.request.user.has_perms(perms, obj=obj)

    def get_form_class(self):
        if self.model_form is not None:
            return self.model_form
        return super().get_form_class()

    def get_queryset(self):
        if self.queryset is None:
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} is missing a QuerySet. Define "
                f"{self.__class__.__name__}.queryset."
            )
        queryset = self.queryset.all()
        if hasattr(queryset, 'filter_by_tenant'):
            queryset = queryset.filter_by_tenant()
        return queryset

    def get_object(self):
        # Cached: this is called from has_permission, get_form_kwargs,
        # form_valid and get_context_data within a single request.
        if getattr(self, '_cached_object', None) is None:
            self._cached_object = get_object_or_404(self.get_queryset(), pk=self.kwargs.get('pk'))
        return self._cached_object

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.get_object()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context[self.context_object_name] = self.get_object()
        return context

    def get_service_kwargs(self, form):
        service_kwargs = {}
        for key, value in form.cleaned_data.items():
            if key in self.form_exclude_fields:
                continue
            mapped_key = self.form_field_map.get(key, key)
            service_kwargs[mapped_key] = value
        return service_kwargs

    def post_service(self, obj, form, result):
        """Hook for subclasses: runs inside the same transaction, after
        ``service_callable`` succeeded."""

    def form_valid(self, form):
        obj = self.get_object()
        try:
            with transaction.atomic():
                result = self.__class__.service_callable(
                    obj, user=self.request.user, request=self.request,
                    **self.get_service_kwargs(form)
                )
                self.post_service(obj, form, result)

            if getattr(self.request, 'htmx', False):
                if self.hx_redirect_on_success:
                    # Full navigation follows, so queue a Django message for the
                    # next render instead of a toast trigger.
                    messages.success(self.request, self.get_success_message(result))
                    response = HttpResponse(status=204)
                    response['HX-Redirect'] = obj.get_absolute_url()
                    return response
                return self._htmx_success_response(obj, result)
            messages.success(self.request, self.get_success_message(result))
            return redirect(obj.get_absolute_url())

        except ValidationError as e:
            for msg in e.messages:
                form.add_error(None, msg)
            return self.form_invalid(form)
        except Exception as e:
            logger.exception("Unexpected error in %s.form_valid", self.__class__.__name__)
            form.add_error(None, _("An unexpected error occurred. Please try again or contact support."))
            return self.form_invalid(form)

    def form_invalid(self, form):
        if getattr(self.request, 'htmx', False) and self.error_partial:
            response = render(
                self.request, self.error_partial, self.get_context_data(form=form)
            )
            # 422 signals a validation failure; the client opts this status into
            # swapping (htmx:beforeSwap handler in static/src/state.ts).
            response.status_code = 422
            return response
        return super().form_invalid(form)

    def _htmx_success_response(self, obj, result=None):
        response = HttpResponse(status=204)
        trigger_data = {
            "closeModalEvent": None,
            self.hx_trigger: None,
            "showMessage": {
                "message": str(self.get_success_message(result)),
                "level": "success"
            }
        }
        response['HX-Trigger'] = json.dumps(trigger_data)
        return response

    def get_success_message(self, result=None):
        return self.success_message


class SimplePostView(PermissionRequiredMixin, LoginRequiredMixin, View):
    queryset = None
    hx_trigger = "tableRefreshRequired"

    def get_permission_required(self):
        # Fail closed: a service/action view that mutates state must declare the
        # permission(s) it requires. A missing (None) permission_required is a
        # developer error, not an open door — historically it silently allowed
        # ANY authenticated tenant member to run the action (B3). Views that
        # intentionally perform their own per-object authorization (e.g. an
        # ownership check inside perform_action/form_valid) opt out explicitly by
        # setting `permission_required = ()`.
        if self.permission_required is None:
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} is missing permission_required. Set it "
                f"to the required permission(s), or to an empty tuple () to opt into "
                f"handling authorization itself."
            )
        if isinstance(self.permission_required, str):
            return (self.permission_required,)
        return self.permission_required

    def has_permission(self):
        perms = self.get_permission_required()
        if not perms:
            return True
        try:
            obj = self.get_object()
        except Http404:
            if self.request.user.is_authenticated:
                raise
            obj = None
        return self.request.user.has_perms(perms, obj=obj)

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        try:
            result = self.perform_action(obj, request)
            if getattr(request, 'htmx', False):
                return self._htmx_success_response(obj, result)
            messages.success(request, result.get('message', _('Action completed successfully.')))
            return self.get_success_redirect(obj, result)
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                msg = "; ".join([f"{k}: {', '.join(v)}" for k, v in e.message_dict.items()])
            elif hasattr(e, 'messages'):
                msg = "; ".join(e.messages)
            else:
                msg = str(e)
            
            if getattr(request, 'htmx', False):
                return self._htmx_error_response(msg)
            messages.error(request, msg)
            return redirect(obj.get_absolute_url())

    def get_queryset(self):
        if self.queryset is None:
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} is missing a QuerySet. Define "
                f"{self.__class__.__name__}.queryset."
            )
        queryset = self.queryset.all()
        if hasattr(queryset, 'filter_by_tenant'):
            queryset = queryset.filter_by_tenant()
        return queryset

    def get_object(self):
        if getattr(self, '_cached_object', None) is not None:
            return self._cached_object
        pk = self.kwargs.get('pk')
        if pk is not None:
            self._cached_object = get_object_or_404(self.get_queryset(), pk=pk)
            return self._cached_object
        raise NotImplementedError(
            f"{self.__class__.__name__} must define 'queryset' or override get_object()"
        )

    def perform_action(self, obj, request):
        raise NotImplementedError

    def get_success_redirect(self, obj, result):
        return redirect(obj.get_absolute_url())

    def _htmx_success_response(self, obj, result):
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps({
            "closeModalEvent": None,
            self.hx_trigger: None,
            "showMessage": {
                "message": str(result.get('message', _('Done.'))),
                "level": "success"
            }
        })
        return response

    def _htmx_error_response(self, message):
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps({
            "showMessage": {
                "message": message,
                "level": "danger"
            }
        })
        return response
