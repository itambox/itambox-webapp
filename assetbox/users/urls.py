from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('profile/', views.UserProfileView.as_view(), name='user_profile'),
    path('password/', views.UserPasswordView.as_view(), name='user_password'),
    path('preferences/', views.UserPreferencesView.as_view(), name='user_preferences'),
    path('api-tokens/', views.UserApiTokensView.as_view(), name='user_api_tokens'),
    path('notifications/', views.UserNotificationsView.as_view(), name='user_notifications'), 
    path('subscriptions/', views.UserSubscriptionsView.as_view(), name='user_subscriptions'),
] 