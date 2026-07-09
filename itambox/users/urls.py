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
    path('bookmarks/', views.UserBookmarksView.as_view(), name='user_bookmarks'),
    path('subscriptions/', views.UserSubscriptionsView.as_view(), name='user_subscriptions'),
    path('bookmarks/toggle/<int:content_type_id>/<int:object_id>/', views.BookmarkToggleView.as_view(), name='bookmark_toggle'),
    path('watches/toggle/<int:content_type_id>/<int:object_id>/', views.WatchToggleView.as_view(), name='watch_toggle'),

    # User Management Views (Frontend Admin)
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('users/add/', views.UserEditView.as_view(), name='user_create'),
    path('users/edit/', views.UserBulkEditView.as_view(), name='user_bulk_edit'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user_detail'),
    path('users/<int:pk>/edit/', views.UserEditView.as_view(), name='user_update'),
    path('users/<int:pk>/delete/', views.UserDeleteView.as_view(), name='user_delete'),

    # User Groups (global, cross-tenant; relocated from organization/)
    path('user-groups/', views.UserGroupListView.as_view(), name='usergroup_list'),
    path('user-groups/add/', views.UserGroupEditView.as_view(), name='usergroup_create'),
    path('user-groups/delete/', views.UserGroupBulkDeleteView.as_view(), name='usergroup_bulk_delete'),
    path('user-groups/<int:pk>/', views.UserGroupDetailView.as_view(), name='usergroup_detail'),
    path('user-groups/<int:pk>/edit/', views.UserGroupEditView.as_view(), name='usergroup_update'),
    path('user-groups/<int:pk>/delete/', views.UserGroupDeleteView.as_view(), name='usergroup_delete'),
    path('user-groups/<int:pk>/assign/', views.UserGroupAssignUsersView.as_view(), name='usergroup_assign_users'),
] 