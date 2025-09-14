from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('profile/', views.UserProfileView.as_view(), name='user_profile'),
    path('password/', views.UserPasswordView.as_view(), name='user_password'),
    path('preferences/', views.UserPreferencesView.as_view(), name='user_preferences'),
    path('api-tokens/', views.UserApiTokensView.as_view(), name='user_api_tokens'),
    path('api-tokens/<int:pk>/delete/', views.delete_api_token, name='delete_api_token'),
    path('notifications/', views.UserNotificationsView.as_view(), name='user_notifications'), 
    path('notifications/poll/', views.notification_poll, name='notification_poll'),
    path('notifications/<int:pk>/read/', views.mark_notification_read, name='mark_notification_read'),
    path('notifications/<int:pk>/view/', views.view_notification, name='view_notification'),
    path('notifications/read-all/', views.mark_all_notifications_read, name='mark_all_notifications_read'),
    path('subscriptions/', views.UserSubscriptionsView.as_view(), name='user_subscriptions'),
    path('bookmarks/toggle/<int:content_type_id>/<int:object_id>/', views.bookmark_toggle, name='bookmark_toggle'),
] 