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
]