import platform

from django import __version__ as DJANGO_VERSION
from django.conf import settings
from django.contrib.auth import get_user_model
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from core.api.permissions import IsAuthenticatedOrLoginNotRequired
from core.api.viewsets import ITAMBoxReadOnlyModelViewSet
from core.api.serializers import ObjectChangeSerializer
from core.models import ObjectChange

User = get_user_model()


class APIRootView(APIView):
    _ignore_model_permissions = True

    def get_view_name(self):
        return "API Root"

    @extend_schema(exclude=True)
    def get(self, request, format=None):
        return Response({
            'assets': reverse('api:assets_api:api-root', request=request, format=format),
            'core': reverse('api:core_api:api-root', request=request, format=format),
            'extras': reverse('api:extras_api:api-root', request=request, format=format),
            'licenses': reverse('api:licenses_api:api-root', request=request, format=format),
            'organization': reverse('api:organization_api:api-root', request=request, format=format),
            'software': reverse('api:software_api:api-root', request=request, format=format),
            'status': reverse('api:api-status', request=request, format=format),
            'subscriptions': reverse('api:subscriptions_api:api-root', request=request, format=format),
            'users': reverse('api:users_api:api-root', request=request, format=format),
        })


class StatusView(APIView):
    permission_classes = [IsAuthenticatedOrLoginNotRequired]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response({
            'django-version': DJANGO_VERSION,
            'itambox-version': getattr(settings, 'VERSION', 'unknown'),
            'python-version': platform.python_version(),
        })


class AuthenticationCheckView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        from users.api.serializers import UserSerializer
        serializer = UserSerializer(request.user, context={'request': request})
        return Response(serializer.data)


class ObjectChangeViewSet(ITAMBoxReadOnlyModelViewSet):
    queryset = ObjectChange.objects.select_related('user', 'changed_object_type').all()
    serializer_class = ObjectChangeSerializer
    filterset_fields = ['user_id', 'action', 'changed_object_type_id', 'changed_object_id']
