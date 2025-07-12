from django.urls import path

from extras.dashboard import views as dashboard_views

urlpatterns = [
    path('widgets/add/', dashboard_views.DashboardWidgetAddView.as_view(), name='dashboard_widget_add'),
    path('widgets/<int:index>/config/', dashboard_views.DashboardWidgetConfigView.as_view(), name='dashboard_widget_config'),
    path('widgets/<int:index>/delete/', dashboard_views.DashboardWidgetDeleteView.as_view(), name='dashboard_widget_delete'),
    path('reset/', dashboard_views.DashboardResetView.as_view(), name='dashboard_reset'),
    path('save-layout/', dashboard_views.DashboardSaveLayoutView.as_view(), name='dashboard_save_layout'),
]
