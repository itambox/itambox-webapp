from django.urls import path
from . import views

app_name = 'scim'

urlpatterns = [
    path('ServiceProviderConfig', views.ServiceProviderConfigView.as_view(), name='service-provider-config'),
    path('Users', views.SCIMUserListView.as_view(), name='user-list'),
    path('Users/<int:pk>', views.SCIMUserDetailView.as_view(), name='user-detail'),
    path('Groups', views.SCIMGroupListView.as_view(), name='group-list'),
    path('Groups/<int:pk>', views.SCIMGroupDetailView.as_view(), name='group-detail'),
]
