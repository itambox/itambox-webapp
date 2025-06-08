# core/api/views.py
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from core.models import ObjectChange
from .serializers import ObjectChangeSerializer

class ObjectChangeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API endpoint for viewing ObjectChanges (changelog).
    """
    queryset = ObjectChange.objects.all().prefetch_related(
        'user', 'changed_object_type', 'related_object_type'
    )
    serializer_class = ObjectChangeSerializer
    permission_classes = [IsAdminUser] # Adjust permissions as needed