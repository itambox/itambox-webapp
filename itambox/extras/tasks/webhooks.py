"""Durable webhook delivery state machine owned by the extras domain.

A producer always persists the ``WebhookDelivery`` row before enqueueing; the
worker receives identity assertions only. The locked row — never the task
payload — is the sole authority for endpoint, event, tenant, test-send state
and the execution payload it derives from them.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import random
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import requests
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django_q.models import Schedule
from django_q.tasks import async_task

from core.crypto import encrypt_string, get_fernet
from core.events import DeliveryDisposition, DeliveryResult, delivery_log_context, delivery_log_message
from extras.models import Event, WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)
WEBHOOK_ENVELOPE_SCHEMA_VERSION = 1
WEBHOOK_TASK_PATH = "extras.tasks.webhooks.send_webhook_task"

_SAFE_REJECTED_MESSAGE = "Webhook delivery was rejected."
_SAFE_CONFIGURATION_MESSAGE = "Invalid webhook configuration."
_SAFE_UNAVAILABLE_MESSAGE = "The external integration is temporarily unavailable."
_SAFE_RETRY_EXHAUSTED_MESSAGE = "The external integration remained unavailable; retry the operation later."
_SAFE_IN_PROGRESS_MESSAGE = "Delivery is still in progress."
_SAFE_IDENTITY_MESSAGE = "Webhook delivery was rejected."
_CONFIGURATION_ERROR_CLASS = "integration.configuration"
_REQUEST_ERROR_CLASS = "integration.request_rejected"
_UNAVAILABLE_ERROR_CLASS = "integration.unavailable"
_RETRY_EXHAUSTED_ERROR_CLASS = "integration.retry_budget_exhausted"
_IDENTITY_ERROR_CLASS = "integration.delivery_identity_rejected"

# The default django-q worker timeout is 600 seconds and its broker retry is
# 660 seconds. A crashed worker therefore loses this lease before redelivery,
# while a still-live task remains fenced for its entire allowed runtime.
_CLAIM_LEASE_SECONDS = 600


@dataclass(frozen=True, slots=True)
class WebhookDeliveryAssertions:
    """Immutable identity claims a task makes about its durable delivery row.

    Every field is required and compared exactly — including ``None`` and type —
    against the locked row before any lookup, mutation, DNS resolution or send.
    """

    delivery_pk: int
    delivery_id: UUID
    webhook_endpoint_id: int | None
    event_id: int | None
    tenant_id: int | None
    test_send: bool

    def as_task_value(self) -> dict[str, object]:
        """Literal-only representation for a django-q ``Schedule.kwargs`` payload."""
        return {
            "delivery_pk": self.delivery_pk,
            "delivery_id": str(self.delivery_id),
            "webhook_endpoint_id": self.webhook_endpoint_id,
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "test_send": self.test_send,
        }


_ASSERTION_FIELDS = (
    "delivery_pk",
    "delivery_id",
    "webhook_endpoint_id",
    "event_id",
    "tenant_id",
    "test_send",
)


@dataclass(frozen=True, slots=True)
class _ExecutionPlan:
    """Everything a send needs, derived from the locked row and its relations."""

    url: str
    method: str
    headers: Mapping[str, str]
    secret: str
    retry_count: int
    retry_backoff: int
    event_action: str
    event_model_app_label: str
    event_model_name: str
    event_object_id: int | str
    event_timestamp_iso: str
    event_data: Mapping[str, object]


def _webhook_envelope(*, event_id, delivery_id, attempt, tenant_id):
    """Return the stable v1 metadata shared by every webhook payload format."""
    return {
        "schema_version": WEBHOOK_ENVELOPE_SCHEMA_VERSION,
        "event_id": event_id,
        "delivery_id": delivery_id,
        # The task retry counter is zero-based internally; the wire contract is
        # deliberately one-based so the first delivery is attempt 1.
        "attempt": attempt + 1,
        "tenant": tenant_id,
    }


def _safe_response_code(response) -> int | None:
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) and status_code > 0 else None


def backoff_seconds(attempt: int, retry_backoff: int) -> float:
    """Return capped exponential backoff with the required twenty-percent jitter."""
    base = min(max(0, retry_backoff) * (2 ** max(0, attempt - 1)), 3600)
    return max(1.0, base * random.uniform(0.8, 1.2))


def _actor_for_id(actor_id: int | None):
    if actor_id is None:
        return None
    # inline import: app-registry: resolve the configured user model only when a task has an actor
    from django.contrib.auth import get_user_model

    return get_user_model()._default_manager.filter(pk=actor_id).first()


def _has_permission(user, permission: str) -> bool:
    try:
        return bool(user and getattr(user, "is_authenticated", False) and user.has_perm(permission))
    except (AttributeError, TypeError):
        return False


def _is_platform_actor(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (getattr(user, "is_superuser", False) or _has_permission(user, "extras.view_webhookdelivery"))
    )


def _configuration_result(message: str = _SAFE_CONFIGURATION_MESSAGE) -> DeliveryResult:
    return DeliveryResult(
        "webhook.deliver",
        DeliveryDisposition.TERMINAL,
        True,
        message,
        _CONFIGURATION_ERROR_CLASS,
    )


def _identity_result() -> DeliveryResult:
    return DeliveryResult(
        "webhook.deliver",
        DeliveryDisposition.TERMINAL,
        True,
        _SAFE_IDENTITY_MESSAGE,
        _IDENTITY_ERROR_CLASS,
    )


def _supplied_field(value, name):
    """Read one claimed field from a value object or mapping without raising."""
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _parse_assertions(value):
    """Return the typed assertions for ``value``, or ``(None, reason)``.

    Normalization is confined to this parser and never touches the database: a
    string UUID becomes a ``UUID`` for comparison, and nothing else is coerced —
    ``"1"`` is not integer ``1``.
    """
    if isinstance(value, WebhookDeliveryAssertions):
        claimed = {name: getattr(value, name) for name in _ASSERTION_FIELDS}
    elif isinstance(value, Mapping) and set(value) == set(_ASSERTION_FIELDS):
        claimed = dict(value)
    else:
        return None, "assertions_missing"

    delivery_pk = claimed["delivery_pk"]
    if not isinstance(delivery_pk, int) or isinstance(delivery_pk, bool):
        return None, "delivery_pk_invalid"

    raw_delivery_id = claimed["delivery_id"]
    if isinstance(raw_delivery_id, UUID):
        delivery_id = raw_delivery_id
    elif isinstance(raw_delivery_id, str):
        try:
            delivery_id = UUID(raw_delivery_id)
        except ValueError:
            return None, "delivery_id_invalid"
    else:
        return None, "delivery_id_invalid"

    return (
        WebhookDeliveryAssertions(
            delivery_pk=delivery_pk,
            delivery_id=delivery_id,
            webhook_endpoint_id=claimed["webhook_endpoint_id"],
            event_id=claimed["event_id"],
            tenant_id=claimed["tenant_id"],
            test_send=claimed["test_send"],
        ),
        None,
    )


def _identity_mismatches(assertions: WebhookDeliveryAssertions, delivery) -> tuple[str, ...]:
    """Field names whose claimed value differs from the locked row (exact, typed)."""
    try:
        canonical_delivery_id = UUID(str(delivery.delivery_id))
    except (TypeError, ValueError):
        canonical_delivery_id = None
    claimed = (
        ("delivery_id", assertions.delivery_id, canonical_delivery_id),
        ("webhook_endpoint_id", assertions.webhook_endpoint_id, delivery.endpoint_id),
        ("event_id", assertions.event_id, delivery.event_id),
        ("tenant_id", assertions.tenant_id, delivery.tenant_id),
        ("test_send", assertions.test_send, delivery.test_send),
    )
    return tuple(
        name for name, supplied, canonical in claimed if type(supplied) is not type(canonical) or supplied != canonical
    )


def _reject_identity(supplied, delivery, reason: str, mismatched: tuple[str, ...]) -> DeliveryResult:
    """Emit one sanitized security-audit record and return the terminal result.

    Only stable codes, parseable identities and mismatched field names may appear:
    never a URL, header, payload, exception text or secret.
    """
    supplied_pk = _supplied_field(supplied, "delivery_pk")
    supplied_uuid = _supplied_field(supplied, "delivery_id")
    logger.warning(
        "operation=webhook.identity_assert disposition=terminal error_class=%s reason=%s "
        "supplied_delivery_pk=%s supplied_delivery_id=%s canonical_delivery_pk=%s canonical_delivery_id=%s "
        "mismatched_fields=%s",
        _IDENTITY_ERROR_CLASS,
        reason,
        supplied_pk if isinstance(supplied_pk, int) and not isinstance(supplied_pk, bool) else "",
        supplied_uuid if isinstance(supplied_uuid, UUID) else "",
        getattr(delivery, "pk", ""),
        getattr(delivery, "delivery_id", ""),
        ",".join(mismatched),
    )
    return _identity_result()


def _mark_dead_locked(delivery, *, error_class: str, error_message: str, response_code: int | None = None) -> None:
    now = timezone.now()
    delivery.status = "dead"
    delivery.response_code = response_code
    delivery.error_class = error_class
    delivery.error_message = error_message
    delivery.next_retry_at = None
    delivery.completed_at = now
    delivery.claim_token = None
    delivery.claim_expires_at = None
    delivery.save(
        update_fields=[
            "status",
            "response_code",
            "error_class",
            "error_message",
            "next_retry_at",
            "completed_at",
            "claim_token",
            "claim_expires_at",
            "updated_at",
        ]
    )


def _event_payload_fields(delivery, event):
    """Payload provenance for the locked row: test envelope or its own event."""
    if delivery.test_send:
        return (
            "test",
            "extras",
            "WebhookEndpoint",
            delivery.endpoint_id or "",
            timezone.now().isoformat(),
            {},
        )
    if event is None:
        return None
    return (
        event.action,
        event.model.app_label,
        event.model.model,
        event.object_id,
        event.timestamp.isoformat(),
        event.data,
    )


def _decrypt_target_secret(value):
    """Decrypt a durable secret snapshot without logging malformed ciphertext."""
    if not value:
        return ""
    if not isinstance(value, str) or not value.startswith("enc$"):
        return None
    try:
        return get_fernet().decrypt(value[4:].encode("ascii")).decode("utf-8")
    # broad except: boundary-isolation: opaque ciphertext/keyring failures must fail closed without details
    except Exception:
        return None


def _execution_plan(delivery, event) -> _ExecutionPlan | None:
    """Derive the whole send only from the locked row and its event payload."""
    payload_fields = _event_payload_fields(delivery, event)
    if payload_fields is None:
        return None

    url = delivery.target_url
    method = (delivery.target_http_method or WebhookEndpoint.HTTP_POST).upper()
    headers = delivery.target_headers
    secret = _decrypt_target_secret(delivery.target_secret)
    if (
        not url
        or not delivery.target_enabled
        or (delivery.target_tenant_id is not None and delivery.target_tenant_id != delivery.tenant_id)
        or method not in dict(WebhookEndpoint.METHOD_CHOICES)
        or not isinstance(headers, Mapping)
        or secret is None
    ):
        return None

    action, app_label, model_name, object_id, timestamp_iso, data = payload_fields
    return _ExecutionPlan(
        url=url,
        method=method,
        headers=dict(headers),
        secret=secret,
        retry_count=delivery.target_retry_count,
        retry_backoff=delivery.target_retry_backoff,
        event_action=action,
        event_model_app_label=app_label,
        event_model_name=model_name,
        event_object_id=object_id,
        event_timestamp_iso=timestamp_iso,
        event_data=data,
    )


def _invalid_target(plan) -> bool:
    """Whether the locked row lacks a complete immutable target snapshot."""
    return plan is None


def _claim_delivery(assertions: WebhookDeliveryAssertions, *, attempt: int):
    """Lock the claimed row, prove exact identity parity, then claim the attempt.

    Returns ``(delivery, plan, result)``: ``result`` is ``None`` when the caller
    should send, and otherwise the terminal/no-op result to return unchanged.
    """
    with transaction.atomic():
        delivery = (
            WebhookDelivery._base_manager.select_for_update(of=("self",)).filter(pk=assertions.delivery_pk).first()
        )
        if delivery is None:
            return None, None, _reject_identity(assertions, None, "delivery_unknown", ())

        mismatched = _identity_mismatches(assertions, delivery)
        if mismatched:
            return delivery, None, _reject_identity(assertions, delivery, "identity_mismatch", mismatched)

        if delivery.status in ("success", "dead"):
            return delivery, None, DeliveryResult("webhook.deliver", DeliveryDisposition.NOOP)

        now = timezone.now()
        if delivery.claim_token is not None and delivery.claim_expires_at is not None:
            if delivery.claim_expires_at > now:
                return delivery, None, DeliveryResult("webhook.deliver", DeliveryDisposition.NOOP)

        event = None
        if delivery.event_id is not None:
            event = Event._base_manager.select_related("model").filter(pk=delivery.event_id).first()
        plan = _execution_plan(delivery, event)
        if _invalid_target(plan):
            _mark_dead_locked(
                delivery,
                error_class=_CONFIGURATION_ERROR_CLASS,
                error_message=_SAFE_CONFIGURATION_MESSAGE,
            )
            return delivery, None, _configuration_result()

        delivery.claim_token = uuid4()
        delivery.claim_expires_at = now + datetime.timedelta(seconds=_CLAIM_LEASE_SECONDS)
        delivery.attempt = max(delivery.attempt, attempt + 1)
        delivery.attempted_at = now
        delivery.next_retry_at = None
        delivery.save(
            update_fields=[
                "claim_token",
                "claim_expires_at",
                "attempt",
                "attempted_at",
                "next_retry_at",
                "updated_at",
            ]
        )
        return delivery, plan, None


def _retry_kwargs(assertions: WebhookDeliveryAssertions, delivery, *, actor_id, request_id) -> dict[str, object]:
    """Kwargs for one more attempt at the same durable row.

    The assertions travel as literal primitives so a delayed ``Schedule`` row
    stays representable; the worker's typed parser normalizes them back.
    """
    return {
        "assertions": assertions.as_task_value(),
        "attempt": delivery.attempt,
        "actor_id": actor_id,
        "request_id": request_id,
    }


def _finish_delivery(
    *,
    delivery_pk: int,
    claim_token: UUID,
    result: DeliveryResult,
    response_code: int | None,
    retry_count: int,
    retry_backoff: int,
    retry_kwargs: dict[str, object] | None,
) -> DeliveryResult:
    immediate_retry = False
    with transaction.atomic():
        delivery = WebhookDelivery._base_manager.select_for_update().get(pk=delivery_pk)
        if delivery.claim_token != claim_token:
            return DeliveryResult("webhook.deliver", DeliveryDisposition.NOOP)
        delivery.claim_token = None
        delivery.claim_expires_at = None
        if delivery.status in ("success", "dead"):
            delivery.save(update_fields=["claim_token", "claim_expires_at", "updated_at"])
            return DeliveryResult("webhook.deliver", DeliveryDisposition.NOOP)

        now = timezone.now()
        delivery.response_code = response_code
        if result.disposition == DeliveryDisposition.SUCCESS:
            delivery.status = "success"
            delivery.error_class = ""
            delivery.error_message = ""
            delivery.next_retry_at = None
            delivery.completed_at = now
            delivery.save(
                update_fields=[
                    "status",
                    "response_code",
                    "error_class",
                    "error_message",
                    "next_retry_at",
                    "completed_at",
                    "claim_token",
                    "claim_expires_at",
                    "updated_at",
                ]
            )
            return result

        if result.disposition == DeliveryDisposition.TERMINAL:
            delivery.status = "dead"
            delivery.error_class = result.error_class or _CONFIGURATION_ERROR_CLASS
            delivery.error_message = result.user_message or _SAFE_CONFIGURATION_MESSAGE
            delivery.next_retry_at = None
            delivery.completed_at = now
            delivery.save(
                update_fields=[
                    "status",
                    "response_code",
                    "error_class",
                    "error_message",
                    "next_retry_at",
                    "completed_at",
                    "claim_token",
                    "claim_expires_at",
                    "updated_at",
                ]
            )
            return result

        if delivery.attempt >= max(0, retry_count) + 1:
            delivery.status = "dead"
            delivery.error_class = _RETRY_EXHAUSTED_ERROR_CLASS
            delivery.error_message = _SAFE_RETRY_EXHAUSTED_MESSAGE
            delivery.next_retry_at = None
            delivery.completed_at = now
            delivery.save(
                update_fields=[
                    "status",
                    "response_code",
                    "error_class",
                    "error_message",
                    "next_retry_at",
                    "completed_at",
                    "claim_token",
                    "claim_expires_at",
                    "updated_at",
                ]
            )
            return DeliveryResult(
                "webhook.deliver",
                DeliveryDisposition.RETRYABLE,
                error_class=_RETRY_EXHAUSTED_ERROR_CLASS,
            )

        delivery.status = "failed"
        delivery.error_class = _UNAVAILABLE_ERROR_CLASS
        delivery.error_message = _SAFE_UNAVAILABLE_MESSAGE
        if retry_backoff > 0:
            delay = backoff_seconds(delivery.attempt, retry_backoff)
            delivery.next_retry_at = now + datetime.timedelta(seconds=delay)
            delivery.save(
                update_fields=[
                    "status",
                    "response_code",
                    "error_class",
                    "error_message",
                    "next_retry_at",
                    "claim_token",
                    "claim_expires_at",
                    "updated_at",
                ]
            )
            if retry_kwargs is not None:
                Schedule.objects.create(
                    func=WEBHOOK_TASK_PATH,
                    kwargs=repr(retry_kwargs),
                    schedule_type=Schedule.ONCE,
                    next_run=delivery.next_retry_at,
                )
        else:
            delivery.next_retry_at = None
            delivery.save(
                update_fields=[
                    "status",
                    "response_code",
                    "error_class",
                    "error_message",
                    "next_retry_at",
                    "claim_token",
                    "claim_expires_at",
                    "updated_at",
                ]
            )
            immediate_retry = retry_kwargs is not None

    if immediate_retry:
        async_task(WEBHOOK_TASK_PATH, **retry_kwargs)
    return result


def _dispatch_webhook_request(
    *,
    target_kind,
    url,
    method,
    headers,
    secret,
    envelope,
    summary,
    test_send,
    test_fields,
    event_action,
    event_model_app_label,
    event_model_name,
    event_object_id,
    event_timestamp_iso,
    event_data,
):
    """Build the target-format payload and send it through the pinned transport.

    The send-time SSRF guard and DNS pinning live in ``core.http.request_pinned``;
    redirects are never followed and the secret only signs the body — it is never
    part of the payload.
    """
    # inline import: heavy-import: core.http imports core.validators (django-loaded); keep the task
    # module import-light for django-q payload loading.
    from core.http import request_pinned

    if target_kind == "slack":
        payload = {**envelope, "text": summary}
        if test_send:
            payload.update(test_fields)
        return request_pinned("POST", url, json=payload, timeout=10)
    if target_kind == "teams":
        payload = {
            **envelope,
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": summary,
            "themeColor": "0076D7",
            "title": "ITAMbox Notification",
            "text": summary,
        }
        if test_send:
            payload.update(test_fields)
        return request_pinned("POST", url, json=payload, timeout=10)
    payload = (
        {**envelope, **test_fields}
        if test_send
        else {
            "schema_version": envelope["schema_version"],
            "event_id": envelope["event_id"],
            "delivery_id": envelope["delivery_id"],
            "attempt": envelope["attempt"],
            "tenant": envelope["tenant"],
            "event": event_action,
            "model": f"{event_model_app_label}.{event_model_name}",
            "object_id": event_object_id,
            "timestamp": event_timestamp_iso,
            "data": event_data,
        }
    )
    body = json.dumps(payload, default=str)
    req_headers = dict(headers)
    if secret:
        sig = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        req_headers["X-Hub-Signature-256"] = f"sha256={sig}"
    req_headers.setdefault("Content-Type", "application/json")
    return request_pinned(method, url, headers=req_headers, data=body, timeout=10)


def send_webhook_task(
    assertions: object | None = None,
    *,
    attempt: int = 0,
    actor_id: int | None = None,
    request_id: str | None = None,
) -> DeliveryResult:
    """Dispatch one durable webhook delivery named by ``assertions``."""
    parsed, parse_error = _parse_assertions(assertions)
    if parsed is None:
        return _reject_identity(assertions, None, parse_error, ())

    delivery, plan, result = _claim_delivery(parsed, attempt=attempt)
    if result is not None:
        return result
    claim_token = delivery.claim_token

    operation = "webhook.deliver"
    context = delivery_log_context(
        operation,
        tenant_id=delivery.tenant_id,
        actor_id=actor_id,
        request_id=request_id,
        endpoint=plan.url,
    )
    response_code = None
    try:
        # inline import: heavy-import: core.http imports core.validators; defer it off the task module import path.
        from core.http import webhook_target_kind

        target_kind = webhook_target_kind(plan.url)
        envelope = _webhook_envelope(
            event_id=delivery.event_id,
            delivery_id=delivery.delivery_id,
            attempt=delivery.attempt - 1,
            tenant_id=delivery.tenant_id,
        )
        summary = f"Event: {plan.event_action} on {plan.event_model_name} (ID: {plan.event_object_id})"
        test_fields = {
            "event": "test",
            "model": "extras.WebhookEndpoint",
            "object_id": plan.event_object_id,
            "timestamp": plan.event_timestamp_iso,
            "data": {},
        }
        response = _dispatch_webhook_request(
            target_kind=target_kind,
            url=plan.url,
            method=plan.method,
            headers=plan.headers,
            secret=plan.secret,
            envelope=envelope,
            summary=summary,
            test_send=delivery.test_send,
            test_fields=test_fields,
            event_action=plan.event_action,
            event_model_app_label=plan.event_model_app_label,
            event_model_name=plan.event_model_name,
            event_object_id=plan.event_object_id,
            event_timestamp_iso=plan.event_timestamp_iso,
            event_data=plan.event_data,
        )

        response_code = _safe_response_code(response)
        if response_code is not None and 400 <= response_code < 500:
            logger.warning("%s disposition=terminal reason=http_4xx", delivery_log_message(context))
            result = DeliveryResult(
                operation,
                DeliveryDisposition.TERMINAL,
                True,
                _SAFE_REJECTED_MESSAGE,
                _REQUEST_ERROR_CLASS,
            )
            return _finish_delivery(
                delivery_pk=delivery.pk,
                claim_token=claim_token,
                result=result,
                response_code=response_code,
                retry_count=plan.retry_count,
                retry_backoff=plan.retry_backoff,
                retry_kwargs=None,
            )
        response.raise_for_status()
        logger.info("%s disposition=success", delivery_log_message(context))
        return _finish_delivery(
            delivery_pk=delivery.pk,
            claim_token=claim_token,
            result=DeliveryResult(operation, DeliveryDisposition.SUCCESS),
            response_code=response_code,
            retry_count=plan.retry_count,
            retry_backoff=plan.retry_backoff,
            retry_kwargs=None,
        )

    except ValidationError:
        # Blocked by the SSRF guard (internal target, bad scheme, or unresolvable
        # host — fail closed). Final: never retried.
        logger.error("%s disposition=terminal reason=invalid_target", delivery_log_message(context))
        result = _configuration_result()
        return _finish_delivery(
            delivery_pk=delivery.pk,
            claim_token=claim_token,
            result=result,
            response_code=response_code,
            retry_count=plan.retry_count,
            retry_backoff=plan.retry_backoff,
            retry_kwargs=None,
        )
    except requests.RequestException as exc:
        exception_response = getattr(exc, "response", None)
        response_code = response_code or _safe_response_code(exception_response)
        if attempt >= plan.retry_count:
            logger.error("%s disposition=retryable reason=attempt_limit", delivery_log_message(context))
            result = DeliveryResult(
                operation,
                DeliveryDisposition.RETRYABLE,
                error_class=_RETRY_EXHAUSTED_ERROR_CLASS,
            )
            retry_kwargs = None
        else:
            logger.warning(
                "%s disposition=retryable action=retry attempt=%d retry_count=%d",
                delivery_log_message(context),
                delivery.attempt,
                plan.retry_count,
            )
            result = DeliveryResult(
                operation,
                DeliveryDisposition.RETRYABLE,
                error_class=_UNAVAILABLE_ERROR_CLASS,
            )
            retry_kwargs = _retry_kwargs(parsed, delivery, actor_id=actor_id, request_id=request_id)
        return _finish_delivery(
            delivery_pk=delivery.pk,
            claim_token=claim_token,
            result=result,
            response_code=response_code,
            retry_count=plan.retry_count,
            retry_backoff=plan.retry_backoff,
            retry_kwargs=retry_kwargs,
        )


def _load_delivery_for_actor(delivery_pk: int, actor_id: int | None):
    actor = _actor_for_id(actor_id)
    if actor is None:
        raise PermissionDenied("Delivery not found.")
    delivery = (
        WebhookDelivery.objects.visible_to(actor).select_related("endpoint", "event").filter(pk=delivery_pk).first()
    )
    if delivery is None:
        raise PermissionDenied("Delivery not found.")
    return delivery, actor


def _assertions_for(delivery) -> WebhookDeliveryAssertions:
    return WebhookDeliveryAssertions(
        delivery_pk=delivery.pk,
        delivery_id=UUID(str(delivery.delivery_id)),
        webhook_endpoint_id=delivery.endpoint_id,
        event_id=delivery.event_id,
        tenant_id=delivery.tenant_id,
        test_send=delivery.test_send,
    )


def _enqueue_task(delivery, *, actor_id: int | None) -> None:
    assertions = _assertions_for(delivery)
    if getattr(settings, "Q_CLUSTER", {}).get("sync", False):
        async_task(WEBHOOK_TASK_PATH, assertions, actor_id=actor_id)
    else:
        transaction.on_commit(lambda: async_task(WEBHOOK_TASK_PATH, assertions, actor_id=actor_id))


def _encrypted_secret_snapshot(secret):
    if not isinstance(secret, str):
        raise ValidationError(_SAFE_CONFIGURATION_MESSAGE)
    if not secret or secret.startswith("enc$"):
        return secret
    return encrypt_string(secret)


def _endpoint_target_snapshot(endpoint) -> dict[str, object]:
    """Copy an endpoint target into a delivery before its task is enqueued."""
    headers = endpoint.headers or {}
    if not isinstance(headers, Mapping):
        raise ValidationError(_SAFE_CONFIGURATION_MESSAGE)
    return {
        "target_url": endpoint.url,
        "target_http_method": (endpoint.http_method or WebhookEndpoint.HTTP_POST).upper(),
        "target_headers": dict(headers),
        "target_secret": _encrypted_secret_snapshot(endpoint.secret),
        "target_enabled": endpoint.enabled and endpoint.deleted_at is None,
        "target_tenant_id": endpoint.tenant_id,
        "target_retry_count": endpoint.retry_count,
        "target_retry_backoff": endpoint.retry_backoff,
    }


def _validate_redelivery_source(delivery) -> None:
    """A non-test delivery without an event has no payload provenance to replay."""
    if not delivery.target_url or (not delivery.test_send and delivery.event_id is None):
        raise ValidationError(_SAFE_CONFIGURATION_MESSAGE)


def redeliver_webhook_delivery(delivery_pk: int, *, actor_id: int | None = None):
    """Create and enqueue a fresh delivery for an existing delivery outcome."""
    source, actor = _load_delivery_for_actor(delivery_pk, actor_id)
    if source.tenant_id is None and not _is_platform_actor(actor):
        raise PermissionDenied("Delivery not found.")

    now = timezone.now()
    if source.status == "pending" or (source.next_retry_at is not None and source.next_retry_at > now):
        raise ValidationError(_SAFE_IN_PROGRESS_MESSAGE)

    with transaction.atomic():
        source = (
            WebhookDelivery._base_manager.select_for_update(of=("self",))
            .select_related("endpoint", "event")
            .filter(pk=source.pk)
            .first()
        )
        if source is None:
            raise PermissionDenied("Delivery not found.")
        now = timezone.now()
        if source.status == "pending" or (source.next_retry_at is not None and source.next_retry_at > now):
            raise ValidationError(_SAFE_IN_PROGRESS_MESSAGE)

        _validate_redelivery_source(source)
        new_delivery = WebhookDelivery._base_manager.create(
            tenant_id=source.tenant_id,
            endpoint_id=source.endpoint_id,
            event_id=source.event_id,
            event_rule_id=source.event_rule_id,
            target_url=source.target_url,
            target_http_method=source.target_http_method,
            target_headers=source.target_headers,
            target_secret=source.target_secret,
            target_enabled=source.target_enabled,
            target_tenant_id=source.target_tenant_id,
            target_retry_count=source.target_retry_count,
            target_retry_backoff=source.target_retry_backoff,
            delivery_id=str(uuid4()),
            status="pending",
            test_send=source.test_send,
            redelivered_from=source,
            redelivered_by=actor,
            redelivered_at=now,
        )
        _enqueue_task(new_delivery, actor_id=actor_id)
    return new_delivery


def send_webhook_test(endpoint_pk: int, *, actor_id: int | None = None):
    """Create and enqueue a test delivery for an endpoint."""
    actor = _actor_for_id(actor_id)
    if actor is not None and _is_platform_actor(actor):
        endpoint = WebhookEndpoint._base_manager.filter(pk=endpoint_pk, deleted_at__isnull=True).first()
    else:
        endpoint = WebhookEndpoint.objects.filter(pk=endpoint_pk).first()
    if endpoint is None or (endpoint.tenant_id is None and not _is_platform_actor(actor)):
        raise PermissionDenied("Webhook endpoint not found.")

    with transaction.atomic():
        delivery = WebhookDelivery._base_manager.create(
            tenant_id=endpoint.tenant_id,
            endpoint=endpoint,
            event=None,
            delivery_id=str(uuid4()),
            status="pending",
            test_send=True,
            **_endpoint_target_snapshot(endpoint),
        )
        _enqueue_task(delivery, actor_id=actor_id)
    return delivery
