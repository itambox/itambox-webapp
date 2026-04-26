from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import get_object_or_404
from django.views.generic import View

from itambox.views.generic.mixins import ObjectPermissionRequiredMixin


class BaseObjectView(ObjectPermissionRequiredMixin, View):
    queryset = None
    template_name = None

    def dispatch(self, request, *args, **kwargs):
        self.queryset = self.get_queryset(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self, request):
        if self.queryset is None:
            raise ImproperlyConfigured(f"{self.__class__.__name__} needs a queryset.")
        return self.queryset.all()

    def get_object(self, **kwargs):
        return get_object_or_404(self.queryset, **kwargs)

    def get_extra_context(self, request, instance):
        return {}


class BaseMultiObjectView(ObjectPermissionRequiredMixin, View):
    queryset = None
    table = None
    template_name = None

    def dispatch(self, request, *args, **kwargs):
        self.queryset = self.get_queryset(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self, request):
        if self.queryset is None:
            raise ImproperlyConfigured(f"{self.__class__.__name__} needs a queryset.")
        return self.queryset.all()

    def get_extra_context(self, request):
        return {}
