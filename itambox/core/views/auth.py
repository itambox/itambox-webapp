"""Interactive login entry points.

:class:`ITAMboxLoginView` renders the local username/password form together with
one action per genuinely configured external identity provider (see
``core.auth.providers``). Local password login always stays available: no
deployment setting exists that turns it off, so the page never hides it.
"""

from django.contrib.auth import views as auth_views
from djangosaml2 import views as djangosaml2_views

from core.auth.providers import get_login_providers
from core.auth.saml import SAML_TENANT_SESSION_KEY, bind_saml_tenant, restore_saml_tenant
from core.forms.auth import LoginForm
from core.managers import get_current_tenant


class ITAMboxLoginView(auth_views.LoginView):
    """Django's ``LoginView`` plus the configured SSO entry points."""

    template_name = "registration/login.html"
    authentication_form = LoginForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # ``get_redirect_url()`` returns '' for a missing or unsafe ``next``, so
        # only a validated same-origin destination is ever handed to a provider.
        context["sso_providers"] = get_login_providers(self.get_redirect_url())
        return context


class TenantSamlLoginView(djangosaml2_views.LoginView):
    """SAML AuthnRequest initiator bound to one configured tenant.

    djangosaml2's own entry point has nowhere to learn which tenant a login is
    for, so ``load_saml_config`` would fall back to the ``default`` (or first)
    configuration. Binding happens in ``get`` rather than ``dispatch`` so the
    parent's ``dispatch`` decorators stay in place.
    """

    def get(self, request, *args, **kwargs):
        tenant_slug = kwargs.pop("tenant_slug", None)
        if not self.should_prevent_auth(request):
            bind_saml_tenant(request, tenant_slug)
        return super().get(request, *args, **kwargs)


class TenantSamlAcsView(djangosaml2_views.AssertionConsumerServiceView):
    """Assertion consumer that re-activates the tenant the flow started for.

    The IdP posts back to an anonymous endpoint, so without restoring the pin
    the SP configuration, the SAML backend's JIT provisioning and the session's
    active tenant would all resolve to the wrong tenant. ``post`` is overridden
    instead of ``dispatch`` to preserve the parent's ``csrf_exempt``.
    """

    def post(self, request, *args, **kwargs):
        restore_saml_tenant(request)
        return super().post(request, *args, **kwargs)

    def customize_session(self, user, session_info):
        super().customize_session(user, session_info)
        tenant = get_current_tenant()
        if tenant is not None:
            # Land the user in the tenant they authenticated against rather
            # than TenantMiddleware's first-accessible-tenant default.
            self.request.session["active_tenant_id"] = tenant.pk
            self.request.saml_session.pop(SAML_TENANT_SESSION_KEY, None)
