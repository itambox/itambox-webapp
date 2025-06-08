from django.urls import path
from . import views

app_name = 'extras'

urlpatterns = [
    # Tags
    path('tags/', views.tag_list, name='tag_list'),
    path('tags/<int:pk>/', views.tag_detail, name='tag_detail'), # Detail view might not be needed?
    path('tags/add/', views.tag_create, name='tag_create'),
    path('tags/<int:pk>/edit/', views.tag_update, name='tag_update'),
    path('tags/<int:pk>/delete/', views.tag_delete, name='tag_delete'),
] 