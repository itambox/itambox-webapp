from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet
from extras.models import Tag, Dashboard, CustomField, CustomFieldset
from .serializers import TagSerializer, DashboardSerializer, CustomFieldSerializer, CustomFieldsetSerializer


class TagViewSet(AssetBoxModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer


class CustomFieldViewSet(AssetBoxModelViewSet):
    queryset = CustomField.objects.all()
    serializer_class = CustomFieldSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['field_type', 'required']


class CustomFieldsetViewSet(AssetBoxModelViewSet):
    queryset = CustomFieldset.objects.prefetch_related('fields').all()
    serializer_class = CustomFieldsetSerializer


class DashboardViewSet(AssetBoxModelViewSet):
    serializer_class = DashboardSerializer

    def get_queryset(self):
        return Dashboard.objects.select_related('user').filter(user=self.request.user)
