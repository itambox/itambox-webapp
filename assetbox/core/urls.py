"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from assets import views as asset_views # Import the assets views
from . import views as core_views # Import core views, aliased to avoid clash
from django.conf import settings # Import settings
from django.conf.urls.static import static # Import static
# from django.contrib.auth import views as auth_views # Already imported

# Main URL Patterns
urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', asset_views.dashboard, name='dashboard'), # Root path for dashboard

    # Search Path
    path('search/', core_views.SearchView.as_view(), name='search'),

    # UI Paths
    path('assets/', include('assets.urls', namespace='assets')),
    path('organization/', include('organization.urls', namespace='organization')),
    path('extras/', include('extras.urls')),
    path('software/', include('software.urls', namespace='software')),
    path('tables/config/<str:model_name>/', core_views.table_config, name='table_config'),
    path('user/', include('users.urls')), # Include the users app URLs

    # API Paths (prefixed with /api/) - Point directly to the main api.urls
    path('api/', include('assetbox.api.urls', namespace='api')), # Added namespace='api'

    # Path for core app non-API views if any (e.g., User Preferences UI view)
    # path('core/', include('core.urls')), # Example if core had UI views

    # Changelog
    path('changelog/', core_views.ObjectChangeListView.as_view(), name='objectchange_list'),
    path('changelog/<int:pk>/', core_views.ObjectChangeView.as_view(), name='objectchange'),

    # Bulk Actions
    path('bulk-delete/', core_views.bulk_delete, name='bulk_delete'),
    # path('bulk-edit/', core_views.bulk_edit, name='bulk_edit'), # Placeholder for future bulk edit

    # Remove individual user paths from core
    # path('user/profile/', core_views.UserProfileView.as_view(), name='user_profile'),
    # ... (remove other core user paths) ...
]

# # Serve static files during development - Removed as we use CDN for now
# if settings.DEBUG:
#     urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT if hasattr(settings, 'STATIC_ROOT') else settings.STATICFILES_DIRS[0])
