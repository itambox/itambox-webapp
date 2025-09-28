from django.urls import path

from extras.dashboard import views as dashboard_views

urlpatterns = [
    path('<int:dashboard_id>/widgets/add/', dashboard_views.DashboardWidgetAddView.as_view(), name='dashboard_widget_add'),
    path('<int:dashboard_id>/widgets/<int:index>/config/', dashboard_views.DashboardWidgetConfigView.as_view(), name='dashboard_widget_config'),
    path('<int:dashboard_id>/widgets/<int:index>/delete/', dashboard_views.DashboardWidgetDeleteView.as_view(), name='dashboard_widget_delete'),
    path('<int:dashboard_id>/reset/', dashboard_views.DashboardResetView.as_view(), name='dashboard_reset'),
    path('<int:dashboard_id>/save-layout/', dashboard_views.DashboardSaveLayoutView.as_view(), name='dashboard_save_layout'),
    
    # Global Switcher & Dashboard CRUD Management
    path('manage/', dashboard_views.DashboardManageModalView.as_view(), name='dashboard_manage_modal'),
    path('create/', dashboard_views.DashboardCreateView.as_view(), name='dashboard_create'),
    path('<int:pk>/delete/', dashboard_views.DashboardDeleteView.as_view(), name='dashboard_delete_dashboard'),
    path('<int:pk>/rename/', dashboard_views.DashboardRenameView.as_view(), name='dashboard_rename_dashboard'),
    path('<int:pk>/set-default/', dashboard_views.DashboardSetDefaultView.as_view(), name='dashboard_set_default_dashboard'),
]
