from rest_framework.viewsets import ViewSet
from rest_framework.response import Response

class DocuSignViewSet(ViewSet):
    """
    Mock API ViewSet for DocuSign Integration.
    Exposes endpoints at /api/plugins/itambox_esign/.
    """
    permission_classes = []

    def list(self, request):
        return Response({
            "status": "active",
            "message": "DocuSign integration plugin API is online.",
            "sandbox": True
        })
