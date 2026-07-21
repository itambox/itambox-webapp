"""Hardened outbound HTTP for tenant-configured URLs (webhooks, chat notifications).

DNS-rebinding-safe sender: the URL is validated by ``validate_external_url``
(which fails closed on resolution errors) and the TCP connection is PINNED to
the exact address that passed validation, so a second, different DNS answer
between check and use cannot re-route the request to an internal target
(the classic TOCTOU gap this module exists to close). Redirects are disabled:
a 3xx pointing at an internal address would bypass the guard entirely.

For HTTPS the original hostname is preserved as TLS SNI / certificate
verification target via a pinned-SNI transport adapter, and as the HTTP Host
header — the server sees a normal request; only the socket target is fixed.

``request_pinned`` is the single seam through which all tenant-configured
outbound traffic must flow (and the single patch target for tests of callers).
"""
import requests
from requests.adapters import HTTPAdapter
from urllib.parse import urlsplit, urlunsplit

from core.validators import validate_external_url


def webhook_target_kind(url):
    """Classify a webhook URL as 'slack', 'teams', or None by its HOST.

    Payload-format selection only — NOT a security control (SSRF is handled by
    validate_external_url / request_pinned). Parsing the host instead of a bare
    substring match stops ``https://evil.example/hooks.slack.com`` or
    ``https://hooks.slack.com.evil.example`` from being misclassified.
    """
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if host == 'hooks.slack.com':
        return 'slack'
    # Teams incoming webhooks live on a per-tenant <tenant>.webhook.office.com
    # host; legacy connectors use outlook.office.com/webhook.
    if host == 'webhook.office.com' or host.endswith('.webhook.office.com'):
        return 'teams'
    if host == 'outlook.office.com' and parts.path.startswith('/webhook'):
        return 'teams'
    return None


class _PinnedSNIAdapter(HTTPAdapter):
    """Connect to a pinned IP while TLS verifies the ORIGINAL hostname (SNI)."""

    def __init__(self, server_hostname, **kwargs):
        self._server_hostname = server_hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        # urllib3 uses server_hostname both for SNI and as the certificate
        # verification target, so cert checks still run against the real
        # hostname even though the URL now carries the pinned IP.
        pool_kwargs['server_hostname'] = self._server_hostname
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


def request_pinned(method, url, *, headers=None, json=None, data=None, timeout=10):
    """Send an outbound request to a tenant-configured URL, SSRF-hardened.

    Validates ``url`` (raising django ``ValidationError`` on any blocked or
    unresolvable host — fail closed), then connects to the first address that
    passed validation with the hostname preserved for TLS and the Host header.
    Redirects are never followed. Returns the ``requests.Response``.
    """
    resolved = validate_external_url(url)
    parts = urlsplit(url)
    host = parts.hostname

    pinned = resolved[0]
    ip_text = f'[{pinned}]' if pinned.version == 6 else str(pinned)
    port_suffix = f':{parts.port}' if parts.port else ''
    pinned_url = urlunsplit((
        parts.scheme, ip_text + port_suffix, parts.path, parts.query, parts.fragment,
    ))

    req_headers = dict(headers or {})
    req_headers['Host'] = host + port_suffix

    session = requests.Session()
    try:
        if parts.scheme == 'https':
            session.mount('https://', _PinnedSNIAdapter(server_hostname=host))
        return session.request(
            method=method,
            url=pinned_url,
            headers=req_headers,
            json=json,
            data=data,
            timeout=timeout,
            allow_redirects=False,
        )
    finally:
        session.close()
