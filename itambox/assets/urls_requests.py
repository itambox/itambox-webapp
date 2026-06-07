from django.urls import path
from assets.views import request_views

urlpatterns = [
    path('reqs/', request_views.RequestListView.as_view(), name='request_list'),
    path('reqs/add/', request_views.RequestCreateView.as_view(), name='request_create'),
    path('reqs/<int:pk>/', request_views.RequestDetailView.as_view(), name='request_detail'),
    path('reqs/<int:pk>/approve/', request_views.RequestApproveView.as_view(), name='request_approve'),
    path('reqs/<int:pk>/deny/', request_views.RequestDenyView.as_view(), name='request_deny'),
    path('reqs/<int:pk>/cancel/', request_views.RequestCancelView.as_view(), name='request_cancel'),
    path('reqs/<int:pk>/claim/', request_views.RequestClaimView.as_view(), name='request_claim'),
    path('reqs/<int:pk>/mark-fulfilled/', request_views.RequestMarkFulfilledView.as_view(), name='request_mark_fulfilled'),
    path('reqs/bulk-receive/', request_views.RequestBulkReceiveView.as_view(), name='request_bulk_receive'),
]
