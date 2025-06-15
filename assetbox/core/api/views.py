# core/api/views.py
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from collections import OrderedDict
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from core.models import ObjectChange
from .serializers import ObjectChangeSerializer

class APIRootView(APIView):
    """The root view for the AssetBox API."""
    _ignore_model_permissions = True
    exclude_from_schema = True
    swagger_schema = None # Do not include in schema

    def get_view_name(self):
        return "API Root"

    def get(self, request, format=None):
        # Prepare an ordered dictionary of API endpoints
        # The order determines how they appear in the browsable API root
        # Use the full namespace path: <main_api_namespace>:<app_api_namespace>:api-root
        api_root_dict = OrderedDict()
        api_root_dict['assets'] = reverse('api:assets_api:api-root', request=request, format=format)
        api_root_dict['organization'] = reverse('api:organization_api:api-root', request=request, format=format)
        api_root_dict['software'] = reverse('api:software_api:api-root', request=request, format=format)
        api_root_dict['subscriptions'] = reverse('api:subscriptions_api:api-root', request=request, format=format)
        api_root_dict['licenses'] = reverse('api:licenses_api:api-root', request=request, format=format)
        api_root_dict['extras'] = reverse('api:extras_api:api-root', request=request, format=format)
        api_root_dict['core'] = reverse('api:core_api:api-root', request=request, format=format)
        api_root_dict['users'] = reverse('api:users_api:api-root', request=request, format=format) # Assuming users API exists

        return Response(api_root_dict)

class ObjectChangeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API endpoint for viewing ObjectChanges (changelog).
    """
    queryset = ObjectChange.objects.select_related('user', 'changed_object_type').all()
    serializer_class = ObjectChangeSerializer
    filterset_fields = ['user_id', 'action', 'changed_object_type_id', 'changed_object_id']
    permission_classes = [IsAdminUser] # Adjust permissions as needed