from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    # Subscriptions
    path("subscriptions/", views.SubscriptionListView.as_view(), name="subscription_list"),
    path("subscriptions/add/", views.SubscriptionEditView.as_view(), name="subscription_create"),
    path("subscriptions/edit/", views.SubscriptionBulkEditView.as_view(), name="subscription_bulk_edit"),
    path("subscriptions/delete/", views.SubscriptionBulkDeleteView.as_view(), name="subscription_bulk_delete"),
    path("subscriptions/<int:pk>/", views.SubscriptionDetailView.as_view(), name="subscription_detail"),
    path("subscriptions/<int:pk>/edit/", views.SubscriptionEditView.as_view(), name="subscription_update"),
    path("subscriptions/<int:pk>/clone/", views.SubscriptionCloneView.as_view(), name="subscription_clone"),
    path("subscriptions/<int:pk>/delete/", views.SubscriptionDeleteView.as_view(), name="subscription_delete"),
    path("subscriptions/<int:pk>/renew/", views.SubscriptionRenewView.as_view(), name="subscription_renew"),
    path("subscriptions/<int:pk>/cancel/", views.SubscriptionCancelView.as_view(), name="subscription_cancel"),
    path("subscriptions/<int:pk>/suspend/", views.SubscriptionSuspendView.as_view(), name="subscription_suspend"),
    path("subscriptions/<int:pk>/resume/", views.SubscriptionResumeView.as_view(), name="subscription_resume"),
    path("subscriptions/<int:pk>/checkout/", views.SubscriptionCheckoutView.as_view(), name="subscription_checkout"),
    # Subscription Assignments
    path("assignments/add/", views.SubscriptionAssignmentCreateView.as_view(), name="subscriptionassignment_create"),
    path(
        "assignments/<int:pk>/delete/",
        views.SubscriptionAssignmentDeleteView.as_view(),
        name="subscriptionassignment_delete",
    ),
]
