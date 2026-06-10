from django.urls import path, reverse_lazy
from django.views.generic import RedirectView

# Audit session routes moved to compliance app.
# Keep permanent redirects here so old bookmarks and API links still work.
urlpatterns = [
    path('audit-sessions/', RedirectView.as_view(url=reverse_lazy('compliance:auditsession_list'), permanent=True), name='auditsession_list'),
    path('audit-sessions/add/', RedirectView.as_view(url=reverse_lazy('compliance:auditsession_create'), permanent=True), name='auditsession_create'),
    path('audit-sessions/<int:pk>/', RedirectView.as_view(pattern_name='compliance:auditsession_detail', permanent=True), name='auditsession_detail'),
    path('audit-sessions/<int:pk>/scan/', RedirectView.as_view(pattern_name='compliance:auditsession_scan', permanent=True), name='auditsession_scan'),
    path('audit-sessions/<int:pk>/close/', RedirectView.as_view(pattern_name='compliance:auditsession_close', permanent=True), name='auditsession_close'),
    path('audit-sessions/<int:pk>/rehome/', RedirectView.as_view(pattern_name='compliance:auditsession_rehome', permanent=True), name='auditsession_rehome'),
    path('audit-sessions/<int:pk>/delete/', RedirectView.as_view(pattern_name='compliance:auditsession_delete', permanent=True), name='auditsession_delete'),
]
