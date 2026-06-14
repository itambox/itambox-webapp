from django.urls import path
from . import views

app_name = 'procurement'

urlpatterns = [
    # Purchase Orders
    path('orders/', views.PurchaseOrderListView.as_view(), name='purchaseorder_list'),
    path('orders/add/', views.PurchaseOrderEditView.as_view(), name='purchaseorder_create'),
    path('orders/<int:pk>/', views.PurchaseOrderDetailView.as_view(), name='purchaseorder_detail'),
    path('orders/<int:pk>/edit/', views.PurchaseOrderEditView.as_view(), name='purchaseorder_edit'),
    path('orders/<int:pk>/delete/', views.PurchaseOrderDeleteView.as_view(), name='purchaseorder_delete'),

    path('orders/<int:po_pk>/lines/add/', views.PurchaseOrderLineAddView.as_view(), name='purchaseorderline_add'),
    path('lines/<int:pk>/delete/', views.PurchaseOrderLineDeleteView.as_view(), name='purchaseorderline_delete'),
    path('lines/<int:pk>/edit/', views.PurchaseOrderLineEditView.as_view(), name='purchaseorderline_edit'),

    path('orders/<int:pk>/receive/action/', views.PurchaseOrderReceiveView.as_view(), name='purchaseorder_receive'),
    path('orders/<int:pk>/receive/', views.PurchaseOrderReceiveFormView.as_view(), name='purchaseorder_receive_form'),

    path('orders/<int:pk>/approve/', views.PurchaseOrderApproveView.as_view(), name='purchaseorder_approve'),
    path('orders/<int:pk>/order/', views.PurchaseOrderOrderView.as_view(), name='purchaseorder_order'),
    path('orders/<int:pk>/cancel/', views.PurchaseOrderCancelView.as_view(), name='purchaseorder_cancel'),
    path('orders/<int:pk>/reopen/', views.PurchaseOrderReopenView.as_view(), name='purchaseorder_reopen'),

    # Contracts
    path('contracts/', views.ContractListView.as_view(), name='contract_list'),
    path('contracts/add/', views.ContractEditView.as_view(), name='contract_create'),
    path('contracts/<int:pk>/', views.ContractDetailView.as_view(), name='contract_detail'),
    path('contracts/<int:pk>/edit/', views.ContractEditView.as_view(), name='contract_edit'),
    path('contracts/<int:pk>/delete/', views.ContractDeleteView.as_view(), name='contract_delete'),
]
