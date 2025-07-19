from core.api.viewsets import AssetBoxModelViewSet
from extras.models import Tag
from .serializers import TagSerializer


class TagViewSet(AssetBoxModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
