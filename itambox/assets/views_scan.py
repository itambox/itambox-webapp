"""Global scan-to-find endpoint: GET /scan/resolve/?code=<text>"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

from assets.scanning import resolve_scanned_code
from core.managers import get_current_tenant


@method_decorator(login_required, name='dispatch')
class ScanResolveView(View):
    """Resolve a scanned code to an asset URL within the active tenant."""

    def get(self, request, *args, **kwargs):
        # Fail closed: no active tenant means tenant-scoped queries open up.
        if not get_current_tenant():
            return JsonResponse({'found': False}, status=404)

        if not request.user.has_perm('assets.view_asset'):
            return JsonResponse({'found': False}, status=403)

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
