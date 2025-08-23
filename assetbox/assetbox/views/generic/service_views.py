import json
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.generic import FormView, View


class CheckoutView(LoginRequiredMixin, FormView):
    service_function = None
    context_object_name = 'object'
    success_message = "Operation completed successfully."

    def get_context_object(self):
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement get_context_object()"
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs[self.context_object_name] = self.get_context_object()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context[self.context_object_name] = self.get_context_object()
        return context

    def form_valid(self, form):
        obj = self.get_context_object()
        try:
            with transaction.atomic():
                self.service_function(obj, user=self.request.user, **form.cleaned_data)

            if getattr(self.request, 'htmx', False):
                return self._htmx_success_response(obj)
            return redirect(obj.get_absolute_url())

        except Exception as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)

    def _htmx_success_response(self, obj):
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps({
            "closeModalEvent": None,
            "tableRefreshRequired": None,
            "showMessage": {
                "message": self.success_message,
                "level": "success"
            }
        })
        return response


class SimplePostView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        result = self.perform_action(obj, request)
        if getattr(request, 'htmx', False):
            return self._htmx_success_response(obj, result)
        return self.get_success_redirect(obj, result)

    def get_object(self):
        raise NotImplementedError

    def perform_action(self, obj, request):
        raise NotImplementedError

    def get_success_redirect(self, obj, result):
        return redirect(obj.get_absolute_url())

    def _htmx_success_response(self, obj, result):
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps({
            "closeModalEvent": None,
            "tableRefreshRequired": None,
            "showMessage": {
                "message": result.get('message', 'Done.'),
                "level": "success"
            }
        })
        return response
