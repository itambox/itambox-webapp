from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.permissions import BasePermission

from core.api.permissions import TokenPermissions
from core.api.viewsets import AssetBoxModelViewSet
from software.filters import SoftwareFilterSet
from software.models import Software
from .serializers import SoftwareSerializer


class SoftwareViewSet(AssetBoxModelViewSet):
    """API ViewSet for managing Software catalog entries.

    This viewset provides standard CRUD (Create, Read, Update, Delete) endpoints
    and advanced actions for the Software model. It integrates token-based access control,
    optimized database queries using select_related and prefetch_related, and custom
    filtering options.
    """

    permission_classes: list[type[BasePermission]] = [TokenPermissions]
    queryset = Software.objects.select_related('manufacturer').prefetch_related('tags').all()
    serializer_class: type[SoftwareSerializer] = SoftwareSerializer
    filter_backends: tuple = (DjangoFilterBackend,)
    filterset_class: type[SoftwareFilterSet] = SoftwareFilterSet
