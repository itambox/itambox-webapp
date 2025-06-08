# core/api/urls.py
from django.urls import path
# Remove CoreRootView import as it's not defined/needed here
# from .views import CoreRootView 
from rest_framework.routers import DefaultRouter # Import router
from . import views # Import views

app_name = 'core_api'

# --- Use a router for ViewSets like ObjectChangeViewSet --- 
router = DefaultRouter()
router.register(r'object-changes', views.ObjectChangeViewSet, basename='objectchange')

urlpatterns = router.urls

# We don't need a separate CoreRootView URL pattern here.
# The main /api/ root view links to /api/core/, which is handled by the router.
# urlpatterns += [
#     path('', CoreRootView.as_view(), name='api-root'),
# ]