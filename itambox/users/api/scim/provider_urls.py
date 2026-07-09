from django.urls import path
from . import provider_views

app_name = 'provider_scim'

urlpatterns = [
    path('ServiceProviderConfig', provider_views.ProviderServiceProviderConfigView.as_view(), name='service-provider-config'),
    path('Users', provider_views.SCIMProviderUserListView.as_view(), name='user-list'),
    path('Users/<int:pk>', provider_views.SCIMProviderUserDetailView.as_view(), name='user-detail'),
    path('Groups', provider_views.SCIMProviderGroupListView.as_view(), name='group-list'),
    path('Groups/<int:pk>', provider_views.SCIMProviderGroupDetailView.as_view(), name='group-detail'),
]
