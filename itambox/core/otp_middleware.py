"""
OTP enforcement middleware.

Redirects users who *must* use MFA (local password login + superuser/owner/admin
role) to the MFA gate until they have a verified OTP session. SSO/LDAP/SAML/OIDC
sessions and token-authenticated API requests are exempt — see ``core.mfa``.

Runs immediately after ``django_otp.middleware.OTPMiddleware`` so that
``request.user.is_verified()`` is populated.
"""
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.mfa import request_needs_mfa


class OTPEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # Resolved lazily on first request: the URLconf is reliably loaded by
        # then, and reverse() at __init__ can fail under some import orders.
        self._mfa_path = None

    def _allowlist(self):
        """Path prefixes that must never be redirected (avoids redirect loops)."""
        prefixes = [
            self._mfa_path,        # the MFA gate itself
            # Specific auth-flow paths only — NOT the whole '/accounts/' prefix,
            # which would let a password-authenticated but MFA-unverified user
            # reach self-service pages like password_change before completing MFA.
            '/accounts/login',
            '/accounts/logout',
            '/accounts/password_reset',   # password_reset/ and .../done/
            '/accounts/reset/',           # reset/<uidb64>/<token>/ and reset/done/
            '/oidc/',              # OIDC SSO callback prefix
            '/saml2/',             # SAML SSO callback prefix
            '/health',             # health check
        ]
        for url in (settings.STATIC_URL, settings.MEDIA_URL):
            if url:
                prefixes.append(url)
        return tuple(p for p in prefixes if p)

    @staticmethod
    def _is_api_request(request) -> bool:
        """True for requests that can't meaningfully follow a 302 to the HTML MFA
        gate: the ``/api/`` surface and XHR/HTMX/fetch navigations that expect a
        machine-readable response rather than a full-page redirect.
        """
        if request.path.startswith('/api/'):
            return True
        if request.headers.get('HX-Request') == 'true':
            return True
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return True
        # A client that accepts JSON but not HTML is not a browser navigation.
        accept = request.headers.get('Accept', '')
        return 'application/json' in accept and 'text/html' not in accept

    def __call__(self, request):
        # Policy gate: enforcement is opt-in (off in dev/test, on in prod via
        # MFA_ENFORCED). When off we don't force anyone through the gate; the
        # MFA setup/verify pages still work for voluntary enrollment.
        if not getattr(settings, 'MFA_ENFORCED', False):
            return self.get_response(request)

        if self._mfa_path is None:
            self._mfa_path = reverse('mfa_setup')

        user = getattr(request, 'user', None)
        # Guard AnonymousUser / no-user requests up front.
        if user is not None and getattr(user, 'is_authenticated', False):
            path = request.path
            allowlisted = any(path.startswith(prefix) for prefix in self._allowlist())
            if not allowlisted and request_needs_mfa(request):
                # is_verified() is added to the user by django-otp's OTPMiddleware.
                is_verified = getattr(user, 'is_verified', None)
                if not (callable(is_verified) and is_verified()):
                    # API / non-HTML clients can't follow a 302 to the HTML MFA
                    # gate — return a 403 they can surface instead. Normal
                    # browser navigations still get the redirect.
                    if self._is_api_request(request):
                        return JsonResponse(
                            {'detail': str(_('MFA verification required.'))},
                            status=403,
                        )
                    return redirect(reverse('mfa_setup'))

        return self.get_response(request)
