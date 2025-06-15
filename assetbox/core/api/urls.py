# core/api/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views 

app_name = 'core_api'

# Router for core-specific ViewSets
router = DefaultRouter()
router.register(r'object-changes', views.ObjectChangeViewSet, basename='objectchange')
# Register ContentTypeViewSet if needed later
# router.register(r'content-types', views.ContentTypeViewSet, basename='contenttype')

# Only include the router for this app
urlpatterns = [
    path('', include(router.urls)),
]