from django.urls import path
from .views import DocuSignDashboardView, SendEnvelopeView, DocuSignWebhookView

app_name = 'itambox_esign'

urlpatterns = [
    path('dashboard/', DocuSignDashboardView.as_view(), name='dashboard'),
    path('send/<int:asset_id>/', SendEnvelopeView.as_view(), name='send_envelope'),
    path('webhook/', DocuSignWebhookView.as_view(), name='webhook'),
]
