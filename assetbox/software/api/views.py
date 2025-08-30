from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxReadOnlyModelViewSet
from software.models import Software
from software.filters import SoftwareFilterSet
from .serializers import SoftwareSerializer


class SoftwareViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = Software.objects.select_related('manufacturer').prefetch_related('tags').all()
    serializer_class = SoftwareSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SoftwareFilterSet
