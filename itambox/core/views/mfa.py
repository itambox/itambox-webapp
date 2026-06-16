"""TOTP MFA gate for local-password logins.

A single :class:`MFASetupView` handles BOTH enrollment and verification:

* No confirmed ``TOTPDevice`` yet -> show the QR/secret enrollment screen.
* A confirmed device exists -> show the 6-digit verify form.

SSO/LDAP/SAML/OIDC sessions never reach this view because they set a
different auth backend (see ``core.otp_middleware.OTPEnforcementMiddleware``
which only redirects password-login sessions). Enforcement (who is *required*
to set up MFA) lives in ``core.mfa`` / the middleware; this view only renders
the gate and accepts codes.
"""
import base64
import io

import segno
from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import View

from django_otp import login as otp_login, match_token
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from itambox.views.generic.utils import safe_return_url


class MFACodeForm(forms.Form):
    """One-time code (TOTP digits or a backup code) plus a carried ``next``."""

    code = forms.CharField(
        label=_("Authentication code"),
        max_length=64,
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'autofocus': 'autofocus',
            'placeholder': '123456',
        }),
    )
    next = forms.CharField(required=False, widget=forms.HiddenInput)


class MFASetupView(LoginRequiredMixin, View):
    """Enroll-or-verify gate. Redirected to here by the OTP middleware."""

    template_name = 'registration/mfa_setup.html'

    def _safe_next(self, request):
        candidate = request.POST.get('next') or request.GET.get('next')
        return safe_return_url(request, candidate, reverse('dashboard'))

    def _confirmed_device(self, user):
        return TOTPDevice.objects.filter(user=user, confirmed=True).first()

    def _enrollment_context(self, user, safe_next, form=None):
        """Get-or-create an unconfirmed device and build the QR/secret context."""
        device, _created = TOTPDevice.objects.get_or_create(
            user=user, name='default', confirmed=False,
        )
        buf = io.BytesIO()
        segno.make(device.config_url).save(buf, kind='svg')
        qr_src = 'data:image/svg+xml;base64,' + base64.b64encode(buf.getvalue()).decode()
        # bin32 secret is what authenticator apps want for manual entry.
        secret = getattr(device, 'bin_key', None)
        if secret is not None:
            secret = base64.b32encode(device.bin_key).decode('utf-8')
        return {
            'mode': 'enroll',
            'qr_src': qr_src,
            'secret': secret,
            'form': form or MFACodeForm(initial={'next': safe_next}),
            'next': safe_next,
        }

    def _verify_context(self, safe_next, form=None):
        return {
            'mode': 'verify',
            'form': form or MFACodeForm(initial={'next': safe_next}),
            'next': safe_next,
        }

    def get(self, request, *args, **kwargs):
        safe_next = self._safe_next(request)
        if self._confirmed_device(request.user):
            context = self._verify_context(safe_next)
        else:
            context = self._enrollment_context(request.user, safe_next)
        return self.render(request, context)

    def post(self, request, *args, **kwargs):
        safe_next = self._safe_next(request)
        form = MFACodeForm(request.POST)
        if not form.is_valid():
            # Re-render the appropriate screen with field errors.
            if self._confirmed_device(request.user):
                return self.render(request, self._verify_context(safe_next, form))
            return self.render(request, self._enrollment_context(request.user, safe_next, form))

        code = form.cleaned_data['code'].strip()
        confirmed = self._confirmed_device(request.user)

        if confirmed:
            return self._handle_verify(request, code, safe_next, form)
        return self._handle_enroll(request, code, safe_next, form)

    def _handle_enroll(self, request, code, safe_next, form):
        device, _created = TOTPDevice.objects.get_or_create(
            user=request.user, name='default', confirmed=False,
        )
        if not device.verify_token(code):
            form.add_error('code', _("That code is not valid. Please try again."))
            return self.render(request, self._enrollment_context(request.user, safe_next, form))

        device.confirmed = True
        device.save()

        # Issue ten single-use backup codes, replacing any prior set.
        static_device, _sd = StaticDevice.objects.get_or_create(
            user=request.user, name='backup',
        )
        static_device.token_set.all().delete()
        codes = [StaticToken.random_token() for _ in range(10)]
        for token in codes:
            static_device.token_set.create(token=token)

        otp_login(request, device)

        return self.render(request, {
            'mode': 'backup_codes',
            'backup_codes': codes,
            'next': safe_next,
        })

    def _handle_verify(self, request, code, safe_next, form):
        device = match_token(request.user, code)
        if device is None:
            form.add_error('code', _("That code is not valid. Please try again."))
            return self.render(request, self._verify_context(safe_next, form))
        otp_login(request, device)
        return redirect(safe_next)

    def render(self, request, context):
        return render(request, self.template_name, context)
