from django.urls import path, include # Import include
from rest_framework import routers
from . import views

app_name = 'users_api' # Define the app_name for namespacing

router = routers.DefaultRouter()
router.register(r'users', views.UserViewSet)
router.register(r'groups', views.GroupViewSet)
# router.register(r'tokens', views.TokenViewSet) # Uncomment when implemented
# router.register(r'permissions', views.PermissionViewSet) # Uncomment when implemented

urlpatterns = [
    # Custom root view listing all user endpoints
    path('', views.UsersAPIRootView.as_view(), name='api-root'), 
    # Config endpoint
    path('config/', views.UserConfigView.as_view(), name='user-config'),
    # Include router-managed URLs for users, groups etc.
    path('', include(router.urls)), 
] 