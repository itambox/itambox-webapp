# assetbox/api/views.py
# This file can be used for custom API views if needed later.
# The APIRootView is no longer defined here as it's handled by the router.

from collections import OrderedDict
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSetMixin

from django.conf import settings

class APIRootView(APIView):
    # Root view for the AssetBox API.
    _ignore_model_permissions = True

    def get_view_name(self):
        return "API Root"

    def get(self, request, format=None):
        # Generate a dictionary of apps and their endpoints
        # Use fully qualified namespaces because this view is in the 'api' namespace
        api_map = OrderedDict(
            (
                ("assets", reverse("api:assets_api:api-root", request=request, format=format)),
                ("core", reverse("api:core_api:api-root", request=request, format=format)),
                ("organization", reverse("api:organization_api:api-root", request=request, format=format)),
                ("extras", reverse("api:extras_api:api-root", request=request, format=format)),
                # Additional apps can be added here
            )
        )

        return Response(api_map)

# TODO: Implement Status Endpoint like NetBox
class StatusView(ViewSetMixin, APIView):
    pass 