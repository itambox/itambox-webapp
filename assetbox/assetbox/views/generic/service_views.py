import json
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, get_object_or_404
from django.views.generic import FormView, View

from assetbox.views.htmx import BaseHTMXView


class GenericTransactionView(PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, FormView):
    queryset = None
    model_form = None
    service_callable = None
    context_object_name = 'object'
    success_message = "Operation completed successfully."
    hx_trigger = "tableRefreshRequired"
    form_field_map = {}
    form_exclude_fields = ()

    def get_permission_required(self):
        if self.permission_required is None:
            return ()
        if isinstance(self.permission_required, str):
            return (self.permission_required,)
        return self.permission_required

    def has_permission(self):
        perms = self.get_permission_required()
        if not perms:
            return True
        obj = None
        try:
            obj = self.get_object()
        except Exception:
            pass
        return self.request.user.has_perms(perms, obj=obj)

    def get_form_class(self):
        if self.model_form is not None:
            return self.model_form
        return super().get_form_class()

    def get_queryset(self):
        if self.queryset is None:
            from django.core.exceptions import ImproperlyConfigured
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} is missing a QuerySet. Define "
                f"{self.__class__.__name__}.queryset."
            )
        queryset = self.queryset.all()
        if hasattr(queryset, 'filter_by_tenant'):
            queryset = queryset.filter_by_tenant()
        return queryset

    def get_object(self):
        pk = self.kwargs.get('pk')
        return get_object_or_404(self.get_queryset(), pk=pk)

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

    def form_valid(self, form):
        obj = self.get_object()
        try:
            with transaction.atomic():
                result = self.__class__.service_callable(
                    obj, user=self.request.user, request=self.request,
                    **self.get_service_kwargs(form)
                )

            if getattr(self.request, 'htmx', False):
                return self._htmx_success_response(obj, result)
            return redirect(obj.get_absolute_url())

        except Exception as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)

    def _htmx_success_response(self, obj, result=None):
        response = HttpResponse(status=204)
        trigger_data = {
            "closeModalEvent": None,
            self.hx_trigger: None,
            "showMessage": {
                "message": self.get_success_message(result),
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
        if self.permission_required is None:
            return ()
        if isinstance(self.permission_required, str):
            return (self.permission_required,)
        return self.permission_required

    def has_permission(self):
        perms = self.get_permission_required()
        if not perms:
            return True
        obj = None
        try:
            obj = self.get_object()
        except Exception:
            pass
        return self.request.user.has_perms(perms, obj=obj)

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        result = self.perform_action(obj, request)
        if getattr(request, 'htmx', False):
            return self._htmx_success_response(obj, result)
        return self.get_success_redirect(obj, result)

    def get_queryset(self):
        if self.queryset is None:
            from django.core.exceptions import ImproperlyConfigured
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} is missing a QuerySet. Define "
                f"{self.__class__.__name__}.queryset."
            )
        queryset = self.queryset.all()
        if hasattr(queryset, 'filter_by_tenant'):
            queryset = queryset.filter_by_tenant()
        return queryset

    def get_object(self):
        pk = self.kwargs.get('pk')
        if pk is not None:
            return get_object_or_404(self.get_queryset(), pk=pk)
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
                "message": result.get('message', 'Done.'),
                "level": "success"
            }
        })
        return response
