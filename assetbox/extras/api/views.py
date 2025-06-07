from rest_framework import viewsets
from extras.models import Tag
from .serializers import TagSerializer

# Inspired by NetBox API views

class TagViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows Tags to be viewed or edited.
    """
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    # Add filter backends and fields later if needed
    # filter_backends = []
    # filterset_fields = []
