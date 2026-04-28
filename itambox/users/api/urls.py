from django.urls import path, include
from itambox.api.routers import ITAMBoxRouter
from rest_framework.reverse import reverse
from rest_framework.response import Response
from rest_framework.views import APIView
from collections import OrderedDict
from . import views

app_name = 'users_api'

router = ITAMBoxRouter()
router.register(r'users', views.UserViewSet)
router.register(r'groups', views.GroupViewSet)
router.register(r'tokens', views.TokenViewSet, basename='token')

from drf_spectacular.utils import extend_schema

class UsersAPIRootView(APIView):
    _ignore_model_permissions = True

    def get_view_name(self):
        return "Users API Root"

    @extend_schema(
        responses={
            200: {
                "type": "object",
                "properties": {
                    "users": {"type": "string", "format": "uri"},
                    "groups": {"type": "string", "format": "uri"},
                    "config": {"type": "string", "format": "uri"}
                }
            }
        }
    )
    def get(self, request, format=None):
        return Response(OrderedDict((
            ('users', reverse('api:users_api:user-list', request=request, format=format)),
            ('groups', reverse('api:users_api:group-list', request=request, format=format)),
            ('config', reverse('api:users_api:user-config', request=request, format=format)),
        )))

urlpatterns = [
    path('', UsersAPIRootView.as_view(), name='api-root'),
    path('config/', views.UserConfigView.as_view(), name='user-config'),
    path('', include(router.urls)),
]
