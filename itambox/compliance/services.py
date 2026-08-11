import base64
import io
import re
from urllib.parse import urlencode

import segno
from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

from core import events as core_events
from core.events import DeliveryDisposition, DeliveryResult
from core.models import EmailSettings
from extras.models import JournalEntry
from organization.models import Tenant

from .models import CustodyHandoffDelivery, CustodyReceipt, CustodySigningSession

SIGNATURE_PNG_PREFIX = "data:image/png;base64,"
MAX_SIGNATURE_IMAGE_BYTES = 2 * 1024 * 1024
CUSTODY_HANDOFF_QR_BORDER = 4
CUSTODY_HANDOFF_QR_SCALE = 3
CUSTODY_HANDOFF_COOLDOWN_SECONDS = 10
CUSTODY_HANDOFF_SESSION_ATTEMPT_LIMIT = 3
CUSTODY_HANDOFF_RECEIPT_ATTEMPT_LIMIT = 6


class CustodyHandoffNotFound(Exception):
    """The requested receipt/session pair is not visible or not bound."""


class CustodyHandoffPermissionDenied(Exception):
    """The receipt is visible but the actor lacks the prepare permission."""


class CustodyHandoffGone(Exception):
    """A known operator session can no longer mint a handoff credential."""


class CustodyHandoffBoundExceeded(Exception):
    """A durable e-mail-attempt bound refused a new send."""


def build_custody_handoff_url(request, receipt, signing_session):
    """Build the one canonical absolute URL for copy, QR, and e-mail delivery."""
    handoff_path = reverse("compliance:custody_eula_sign", kwargs={"token": receipt.token})
    handoff_path = f"{handoff_path}?{urlencode({'session': signing_session.token})}"
    base_url = getattr(settings, "ITAMBOX_BASE_URL", "")
    if base_url:
        return f"{base_url}{handoff_path}"
    return request.build_absolute_uri(handoff_path)


def resolve_custody_handoff(request, *, receipt_id, session_id):
    """Resolve and re-authorize one live operator handoff in design order."""
    receipt = (
        scope_custody_receipts(
            CustodyReceipt.objects.select_related("asset", "asset__tenant", "holder", "holder__user"),
            user=request.user,
        )
        .filter(pk=receipt_id)
        .first()
    )
    if receipt is None:
        raise CustodyHandoffNotFound

    if not request.user.has_perm("compliance.prepare_custodyreceipt", obj=receipt.asset):
        raise CustodyHandoffPermissionDenied

    signing_session = (
        CustodySigningSession.objects.select_related("operator", "intended_holder", "intended_holder__user")
        .filter(pk=session_id, receipt_id=receipt.pk)
        .first()
    )
    if signing_session is None:
        raise CustodyHandoffNotFound
    if signing_session.operator_id != request.user.pk or signing_session.intended_holder_id != receipt.holder_id:
        raise CustodyHandoffNotFound

    if (
        signing_session.consumed_at is not None
        or signing_session.canceled_at is not None
        or signing_session.expires_at <= timezone.now()
    ):
        raise CustodyHandoffGone
    # This is intentionally last: a non-owner probing a terminal receipt must
    # receive the neutral 404 above rather than learn its terminal state.
    if receipt.acceptance_status != CustodyReceipt.STATUS_PENDING or receipt.holder_id is None:
        raise CustodyHandoffGone
    return receipt, signing_session


def validated_signature_image(signature_canvas):
    """Return a canonical PNG data URI, or ``None`` for unsafe legacy data."""
    if not isinstance(signature_canvas, str) or not signature_canvas.startswith(SIGNATURE_PNG_PREFIX):
        return None
    encoded = signature_canvas[len(SIGNATURE_PNG_PREFIX) :]
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    if len(decoded) > MAX_SIGNATURE_IMAGE_BYTES or not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return SIGNATURE_PNG_PREFIX + base64.b64encode(decoded).decode("ascii")


def render_custody_handoff_qr_svg(handoff_url):
    """Render a token-free SVG artifact from the server-controlled QR serializer."""
    qr = segno.make_qr(handoff_url)
    output = io.BytesIO()
    qr.save(
        output,
        kind="svg",
        scale=CUSTODY_HANDOFF_QR_SCALE,
        border=CUSTODY_HANDOFF_QR_BORDER,
        xmldecl=False,
    )
    return output.getvalue()


def custody_handoff_qr_module_count(handoff_url):
    """Return the symbol module count used to choose its natural rendered size."""
    return segno.make_qr(handoff_url).symbol_size(scale=1, border=0)[0]


def custody_handoff_qr_rendered_size(handoff_url):
    """Return the natural SVG pixel size from the symbol's module count."""
    modules = custody_handoff_qr_module_count(handoff_url)
    return (modules + (CUSTODY_HANDOFF_QR_BORDER * 2)) * CUSTODY_HANDOFF_QR_SCALE


def custody_handoff_email_is_configured():
    email_config = EmailSettings.load()
    return bool(email_config and email_config.enabled and email_config.from_address)


def _holder_language(holder):
    language = settings.LANGUAGE_CODE
    if holder.user_id:
        try:
            stored_language = (holder.user.preferences.data or {}).get("language")
        # broad except: availability-tradeoff: a preferences lookup failure falls back to the default language
        except Exception:
            stored_language = None
        if stored_language in dict(settings.LANGUAGES):
            language = stored_language
    return language


def _normalized_subject(value, *, limit=160):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _custody_handoff_email_content(receipt, signing_session, handoff_url):
    holder = receipt.holder
    asset = receipt.asset
    holder_name = " ".join(part for part in (holder.first_name, holder.last_name) if part).strip() or str(holder)
    expiry = timezone.localtime(signing_session.expires_at).strftime("%Y-%m-%d %H:%M %Z")
    language = _holder_language(holder)
    with translation.override(language):
        subject = _("Custody handoff action: %(name)s (%(tag)s)") % {
            "name": _normalized_subject(asset.name),
            "tag": _normalized_subject(asset.asset_tag),
        }
        body = _(
            "Hello %(holder)s,\n\n"
            "You have a custody handoff waiting for you.\n\n"
            "Asset: %(name)s\n"
            "Asset tag: %(tag)s\n"
            "Serial number: %(serial)s\n\n"
            "Open this one-time handoff link:\n%(url)s\n\n"
            "The session expires at %(expires)s. Sign in as the intended holder before opening the link.\n\n"
            "If you were not expecting this message, ignore it and report it to your IT administrator."
        ) % {
            "holder": holder_name,
            "name": asset.name,
            "tag": asset.asset_tag,
            "serial": asset.serial_number or "N/A",
            "url": handoff_url,
            "expires": expiry,
        }
    return str(subject), str(body), expiry


def send_email_notification(recipients, subject, body, *, tenant_id=None):
    """Proxy the shared transport so tests can patch either service boundary."""
    return core_events.send_email_notification(recipients, subject, body, tenant_id=tenant_id)


def _journal_handoff_event(asset, user, *, state, expires_at):
    expiry = timezone.localtime(expires_at).strftime("%Y-%m-%d %H:%M %Z")
    with transaction.atomic():
        return JournalEntry.objects.create(
            content_object=asset,
            user=user,
            comment=f"Custody handoff e-mail delivery: {state}; expires at {expiry}.",
        )


def _update_handoff_journal(entry, *, state, expires_at):
    expiry = timezone.localtime(expires_at).strftime("%Y-%m-%d %H:%M %Z")
    with transaction.atomic():
        entry.comment = f"Custody handoff e-mail delivery: {state}; expires at {expiry}."
        entry.save(update_fields=["comment", "updated_at"])


def _cooldown_allows_handoff(request, receipt):
    alias = getattr(settings, "RATELIMIT_CACHE", "default")
    key = f"custody-handoff-email:{receipt.asset.tenant_id}:{request.user.pk}:{receipt.pk}"
    try:
        return caches[alias].add(key, True, CUSTODY_HANDOFF_COOLDOWN_SECONDS)
    # broad except: availability-tradeoff: a cache failure fails open so handoff e-mail is not blocked
    except Exception:
        return True


def _book_handoff_delivery(request, receipt, signing_session):
    with transaction.atomic():
        locked_session = (
            CustodySigningSession.objects.select_for_update(of=("self",))
            .select_related("intended_holder", "intended_holder__user", "operator")
            .get(pk=signing_session.pk)
        )
        locked_receipt = (
            CustodyReceipt.objects.select_for_update(of=("self",)).select_related("asset", "holder").get(pk=receipt.pk)
        )
        if (
            locked_session.operator_id != request.user.pk
            or locked_session.intended_holder_id != locked_receipt.holder_id
        ):
            raise CustodyHandoffNotFound
        if (
            locked_session.consumed_at is not None
            or locked_session.canceled_at is not None
            or locked_session.expires_at <= timezone.now()
            or locked_receipt.acceptance_status != CustodyReceipt.STATUS_PENDING
            or locked_receipt.holder_id is None
        ):
            raise CustodyHandoffGone

        session_attempts = CustodyHandoffDelivery.objects.filter(
            signing_session_id=locked_session.pk,
            status__in=(
                CustodyHandoffDelivery.STATUS_REQUESTED,
                CustodyHandoffDelivery.STATUS_SUCCEEDED,
                CustodyHandoffDelivery.STATUS_TERMINAL_FAILED,
            ),
        ).count()
        receipt_attempts = CustodyHandoffDelivery.objects.filter(
            signing_session__receipt_id=locked_receipt.pk,
            status__in=(
                CustodyHandoffDelivery.STATUS_REQUESTED,
                CustodyHandoffDelivery.STATUS_SUCCEEDED,
                CustodyHandoffDelivery.STATUS_TERMINAL_FAILED,
            ),
        ).count()
        if (
            session_attempts >= CUSTODY_HANDOFF_SESSION_ATTEMPT_LIMIT
            or receipt_attempts >= CUSTODY_HANDOFF_RECEIPT_ATTEMPT_LIMIT
        ):
            raise CustodyHandoffBoundExceeded

        delivery = CustodyHandoffDelivery.objects.create(
            receipt=locked_receipt,
            signing_session=locked_session,
            operator=request.user,
            attempt=session_attempts + 1,
            status=CustodyHandoffDelivery.STATUS_REQUESTED,
        )
    return delivery, locked_receipt, locked_session


def _update_handoff_delivery(delivery, result):
    if result.disposition == DeliveryDisposition.SUCCESS:
        delivery.status = CustodyHandoffDelivery.STATUS_SUCCEEDED
        delivery.delivered_at = timezone.now()
        delivery.error_class = None
    elif result.disposition == DeliveryDisposition.TERMINAL:
        delivery.status = CustodyHandoffDelivery.STATUS_TERMINAL_FAILED
        delivery.error_class = result.error_class or "terminal"
    else:
        delivery.error_class = result.error_class
    with transaction.atomic():
        delivery.save(update_fields=["status", "error_class", "delivered_at", "updated_at"])


def _record_refused_handoff(request, receipt, signing_session):
    _journal_handoff_event(
        receipt.asset,
        request.user,
        state="refused",
        expires_at=signing_session.expires_at,
    )


def send_custody_handoff_email(request, receipt, signing_session):
    """Book, send, classify, and journal one holder-bound handoff e-mail."""
    if not receipt.holder or not receipt.holder.email:
        raise ValidationError(_("The holder has no e-mail address."))
    if not custody_handoff_email_is_configured():
        raise ValidationError(_("Email is not configured."))
    if not _cooldown_allows_handoff(request, receipt):
        _record_refused_handoff(request, receipt, signing_session)
        raise ValidationError(_("Please wait before sending another handoff e-mail."))

    try:
        delivery, receipt, signing_session = _book_handoff_delivery(request, receipt, signing_session)
    except CustodyHandoffBoundExceeded:
        _record_refused_handoff(request, receipt, signing_session)
        raise ValidationError(_("The handoff e-mail attempt limit has been reached.")) from None

    handoff_url = build_custody_handoff_url(request, receipt, signing_session)
    subject, body, expiry = _custody_handoff_email_content(receipt, signing_session, handoff_url)
    journal_entry = _journal_handoff_event(
        receipt.asset,
        request.user,
        state="requested",
        expires_at=signing_session.expires_at,
    )
    try:
        result = send_email_notification([receipt.holder.email], subject, body, tenant_id=receipt.asset.tenant_id)
    # broad except: boundary-isolation: an e-mail integration failure is classified as a retryable delivery outcome
    except Exception as exc:
        result = DeliveryResult(
            "email.deliver",
            DeliveryDisposition.RETRYABLE,
            error_class=type(exc).__name__,
        )
    _update_handoff_delivery(delivery, result)

    if result.disposition == DeliveryDisposition.SUCCESS:
        state = "succeeded"
    elif result.disposition == DeliveryDisposition.TERMINAL:
        state = "terminal_failed"
    else:
        state = "retryable_unconfirmed"
    _update_handoff_journal(journal_entry, state=state, expires_at=signing_session.expires_at)
    return {
        "delivery": delivery,
        "result": result,
        "expires_at": expiry,
        "remaining_minutes": max(0, int((signing_session.expires_at - timezone.now()).total_seconds() // 60)),
    }


def scope_custody_receipts(queryset, *, user, permission=None):
    """Limit receipts to asset tenants in the request's authorized tenant scope.

    CustodyReceipt deliberately has no tenant field and keeps an unscoped default
    manager for its public bearer-token route. Every internal caller must therefore
    pass through this helper, which scopes on ``asset__tenant`` and can additionally
    require a permission in each candidate tenant.
    """
    if user is None or not user.is_authenticated:
        return queryset.none()

    candidate_tenants = Tenant.objects.all()
    if permission and not user.is_superuser:
        tenant_ids = [tenant.pk for tenant in candidate_tenants if user.has_perm(permission, obj=tenant)]
    else:
        tenant_ids = candidate_tenants.values_list("pk", flat=True)

    return queryset.filter(asset__tenant_id__in=tenant_ids)
