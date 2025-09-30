from django.urls import path
from . import views

app_name = 'components'

urlpatterns = [
    # Component Catalog (quantity-based)
    path('', views.ComponentListView.as_view(), name='component_list'),
    path('add/', views.ComponentEditView.as_view(), name='component_create'),
    path('<int:pk>/', views.ComponentDetailView.as_view(), name='component_detail'),
    path('<int:pk>/edit/', views.ComponentEditView.as_view(), name='component_update'),
    path('<int:pk>/delete/', views.ComponentDeleteView.as_view(), name='component_delete'),
    path('<int:pk>/clone/', views.ComponentCloneView.as_view(), name='component_clone'),

    # Component Stock
    path('stocks/', views.ComponentStockListView.as_view(), name='componentstock_list'),
    path('stocks/add/', views.ComponentStockEditView.as_view(), name='componentstock_create'),
    path('stocks/<int:pk>/edit/', views.ComponentStockEditView.as_view(), name='componentstock_update'),
    path('stocks/<int:pk>/delete/', views.ComponentStockDeleteView.as_view(), name='componentstock_delete'),

    # Component Allocations
    path('allocations/', views.ComponentAllocationListView.as_view(), name='componentallocation_list'),
    path('allocations/add/', views.ComponentAllocationEditView.as_view(), name='componentallocation_create'),
    path('allocations/<int:pk>/edit/', views.ComponentAllocationEditView.as_view(), name='componentallocation_update'),
    path('allocations/<int:pk>/delete/', views.ComponentAllocationDeleteView.as_view(), name='componentallocation_delete'),
]
