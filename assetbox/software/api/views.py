from core.api.viewsets import AssetBoxReadOnlyModelViewSet
from software.models import Software
from .serializers import SoftwareSerializer


class SoftwareViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = Software.objects.prefetch_related('manufacturer', 'tags').all()
    serializer_class = SoftwareSerializer
