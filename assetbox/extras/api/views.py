from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet
from extras.models import Tag, Dashboard, CustomField, CustomFieldset
from extras.filters import TagFilter, CustomFieldFilterSet, CustomFieldsetFilterSet
from .serializers import TagSerializer, DashboardSerializer, CustomFieldSerializer, CustomFieldsetSerializer


class TagViewSet(AssetBoxModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TagFilter


class CustomFieldViewSet(AssetBoxModelViewSet):
    queryset = CustomField.objects.all()
    serializer_class = CustomFieldSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustomFieldFilterSet


class CustomFieldsetViewSet(AssetBoxModelViewSet):
    queryset = CustomFieldset.objects.prefetch_related('fields').all()
    serializer_class = CustomFieldsetSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustomFieldsetFilterSet


class DashboardViewSet(AssetBoxModelViewSet):
    serializer_class = DashboardSerializer

    def get_queryset(self):
        return Dashboard.objects.select_related('user').filter(user=self.request.user)

