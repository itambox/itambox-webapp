"""Global scan-to-find endpoint: GET /scan/resolve/?code=<text>"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

from assets.scanning import resolve_scanned_target
from core.managers import get_current_tenant


@method_decorator(login_required, name='dispatch')
class ScanResolveView(View):
    """Resolve a scanned code to an asset URL within the active tenant."""

    def get(self, request, *args, **kwargs):
        # Fail closed: no active tenant means tenant-scoped queries open up.
        # Superusers are allowed to bypass this check as they have global view rights.
        if not get_current_tenant() and not request.user.is_superuser:
            return JsonResponse({'found': False}, status=404)


        if not request.user.has_perm('assets.view_asset'):
            return JsonResponse({'found': False}, status=403)

        code = request.GET.get('code', '').strip()
        if not code:
            return JsonResponse({'found': False}, status=400)

        target = resolve_scanned_target(code, request.user)
        if target is None:
            return JsonResponse({'found': False}, status=404)

        return JsonResponse({
            'found': True,
            'url': target['url'],
            'label': target['label'],
        })
