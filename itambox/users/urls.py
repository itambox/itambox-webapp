from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('profile/', views.UserProfileView.as_view(), name='user_profile'),
    path('password/', views.UserPasswordView.as_view(), name='user_password'),
    path('preferences/', views.UserPreferencesView.as_view(), name='user_preferences'),
    path('api-tokens/', views.UserApiTokensView.as_view(), name='user_api_tokens'),
    path('api-tokens/<int:pk>/delete/', views.DeleteApiTokenView.as_view(), name='delete_api_token'),
    path('notifications/', views.UserNotificationsView.as_view(), name='user_notifications'), 
    path('notifications/poll/', views.notification_poll, name='notification_poll'),
    path('notifications/<int:pk>/read/', views.MarkNotificationReadView.as_view(), name='mark_notification_read'),
    path('notifications/<int:pk>/view/', views.ViewNotificationView.as_view(), name='view_notification'),
    path('notifications/read-all/', views.MarkAllNotificationsReadView.as_view(), name='mark_all_notifications_read'),
    path('subscriptions/', views.UserSubscriptionsView.as_view(), name='user_subscriptions'),
    path('bookmarks/toggle/<int:content_type_id>/<int:object_id>/', views.BookmarkToggleView.as_view(), name='bookmark_toggle'),
] 