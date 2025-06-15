from rest_framework import viewsets
from software.models import Software
from .serializers import SoftwareSerializer

class SoftwareViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API endpoint for Software catalog items.
    """
    queryset = Software.objects.prefetch_related('manufacturer', 'tags').all()
    serializer_class = SoftwareSerializer
    # Add filtering/search later if needed
    # filterset_fields = ['manufacturer_id', 'name']
    # search_fields = ['name', 'manufacturer__name', 'description'] 