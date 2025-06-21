from django.urls import path
from . import views

app_name = 'extras'

urlpatterns = [
    # Tags
    path('tags/', views.TagListView.as_view(), name='tag_list'),
    path('tags/create/', views.TagCreateView.as_view(), name='tag_create'),
    path('tags/<int:pk>/', views.TagDetailView.as_view(), name='tag_detail'),
    path('tags/<int:pk>/edit/', views.TagUpdateView.as_view(), name='tag_update'),
    path('tags/<int:pk>/delete/', views.TagDeleteView.as_view(), name='tag_delete'),
    
    # Config Templates
    path('configtemplates/', views.ConfigTemplateListView.as_view(), name='configtemplate_list'),
    path('configtemplates/create/', views.ConfigTemplateCreateView.as_view(), name='configtemplate_create'),
    path('configtemplates/<int:pk>/', views.ConfigTemplateDetailView.as_view(), name='configtemplate_detail'),
    path('configtemplates/<int:pk>/edit/', views.ConfigTemplateUpdateView.as_view(), name='configtemplate_update'),
    path('configtemplates/<int:pk>/delete/', views.ConfigTemplateDeleteView.as_view(), name='configtemplate_delete'),
] 