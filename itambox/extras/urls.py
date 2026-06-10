from django.urls import path, include
from . import views

app_name = 'extras'

urlpatterns = [
    # Tags
    path('tags/', views.TagListView.as_view(), name='tag_list'),
    path('tags/create/', views.TagCreateView.as_view(), name='tag_create'),
    path('tags/<int:pk>/', views.TagDetailView.as_view(), name='tag_detail'),
    path('tags/<int:pk>/edit/', views.TagUpdateView.as_view(), name='tag_update'),
    path('tags/<int:pk>/delete/', views.TagDeleteView.as_view(), name='tag_delete'),
    # Dashboard
    path('dashboard/', include('extras.dashboard.urls')),
    # Custom Fields
    path('custom-fields/', views.CustomFieldListView.as_view(), name='customfield_list'),
    path('custom-fields/add/', views.CustomFieldEditView.as_view(), name='customfield_create'),
    path('custom-fields/<int:pk>/', views.CustomFieldDetailView.as_view(), name='customfield_detail'),
    path('custom-fields/<int:pk>/edit/', views.CustomFieldEditView.as_view(), name='customfield_update'),
    path('custom-fields/<int:pk>/delete/', views.CustomFieldDeleteView.as_view(), name='customfield_delete'),
    # Custom Fieldsets
    path('custom-fieldsets/', views.CustomFieldsetListView.as_view(), name='customfieldset_list'),
    path('custom-fieldsets/add/', views.CustomFieldsetEditView.as_view(), name='customfieldset_create'),
    path('custom-fieldsets/<int:pk>/', views.CustomFieldsetDetailView.as_view(), name='customfieldset_detail'),
    path('custom-fieldsets/<int:pk>/edit/', views.CustomFieldsetEditView.as_view(), name='customfieldset_update'),
    path('custom-fieldsets/<int:pk>/delete/', views.CustomFieldsetDeleteView.as_view(), name='customfieldset_delete'),
    # Config Contexts
    path('config-contexts/', include('extras.urls_contexts')),
]