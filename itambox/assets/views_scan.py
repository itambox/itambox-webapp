"""Global scan-to-find endpoint: GET /scan/resolve/?code=<text>"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

from assets.scanning import resolve_scanned_code


@method_decorator(login_required, name='dispatch')
class ScanResolveView(View):
    """Resolve a scanned code to an asset URL within the active tenant."""

    def get(self, request, *args, **kwargs):
        code = request.GET.get('code', '').strip()
        if not code:
            return JsonResponse({'found': False}, status=400)

        asset = resolve_scanned_code(code)
        if asset is None:
            return JsonResponse({'found': False}, status=404)

        return JsonResponse({
            'found': True,
            'url': asset.get_absolute_url(),
            'label': str(asset),
        })
