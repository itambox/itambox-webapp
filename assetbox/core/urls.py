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
from . import views # Import core views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', asset_views.dashboard, name='dashboard'), # Add root path for dashboard
    path('assets/', include('assets.urls')), # Prefix assets URLs with 'assets/'
    path('organization/', include('organization.urls')),
    path('extras/', include('extras.urls')),
    # Add URL for table configuration
    path('tables/config/<str:model_name>/', views.table_config, name='table_config'),
]
