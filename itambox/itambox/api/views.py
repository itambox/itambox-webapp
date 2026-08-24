import platform

from django import __version__ as DJANGO_VERSION
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, get_resolver
from django.urls.resolvers import URLResolver
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from core.models import ObjectChange
from itambox.api.permissions import IsAuthenticatedOrLoginNotRequired
from itambox.api.serializers import ObjectChangeSerializer
from itambox.api.viewsets import ITAMBoxReadOnlyModelViewSet

User = get_user_model()


def get_api_root_links(request: Request, format: str | None = None) -> dict[str, str]:
    """Build the API root link map from the mounted top-level API namespaces.

    The URL configuration is the single source of truth: the namespaces are
    read from the live resolver at request time, so the root can never drift
    from the routes that are actually mounted. Every mounted namespace that
    exposes its own root view is advertised exactly once, keyed by its URL
    prefix. Mounted namespaces without a root view (e.g. the tenant-scoped
    SCIM endpoints) remain reachable through their direct routes but are not
    discoverable from the API root.
    """
    _, api_resolver = get_resolver(getattr(request, "urlconf", None)).namespace_dict["api"]

    links: dict[str, str] = {}
    for pattern in api_resolver.url_patterns:
        if not isinstance(pattern, URLResolver) or pattern.namespace is None:
            continue
        try:
            url = reverse(f"api:{pattern.namespace}:api-root", request=request, format=format)
        except NoReverseMatch:
            continue
        links[str(pattern.pattern).rstrip("/")] = url

    links["status"] = reverse("api:api-status", request=request, format=format)
    return dict(sorted(links.items()))


class APIRootView(APIView):
    permission_classes = [IsAuthenticated]

    def get_view_name(self):
        return "API Root"

    @extend_schema(exclude=True)
    def get(self, request, format=None):
        return Response(get_api_root_links(request, format))


class StatusView(APIView):
    permission_classes = [IsAuthenticatedOrLoginNotRequired]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(
            {
                "django-version": DJANGO_VERSION,
                "itambox-version": getattr(settings, "VERSION", "unknown"),
                "python-version": platform.python_version(),
            }
        )


class ObjectChangeViewSet(ITAMBoxReadOnlyModelViewSet):
    queryset = ObjectChange.objects.select_related("user", "changed_object_type").all()
    serializer_class = ObjectChangeSerializer
    filterset_fields = ["user_id", "action", "changed_object_type_id", "changed_object_id"]
