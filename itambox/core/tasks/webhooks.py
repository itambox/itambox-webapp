from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import random
from collections.abc import Mapping
from uuid import uuid4

import requests
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django_q.models import Schedule
from django_q.tasks import async_task

from core.events import DeliveryDisposition, DeliveryResult, delivery_log_context, delivery_log_message

logger = logging.getLogger(__name__)
WEBHOOK_ENVELOPE_SCHEMA_VERSION = 1

_SAFE_REJECTED_MESSAGE = "Webhook delivery was rejected."
_SAFE_CONFIGURATION_MESSAGE = "Invalid webhook configuration."
_SAFE_UNAVAILABLE_MESSAGE = "The external integration is temporarily unavailable."
_SAFE_RETRY_EXHAUSTED_MESSAGE = "The external integration remained unavailable; retry the operation later."
_SAFE_IN_PROGRESS_MESSAGE = "Delivery is still in progress."
_CONFIGURATION_ERROR_CLASS = "integration.configuration"
_REQUEST_ERROR_CLASS = "integration.request_rejected"
_UNAVAILABLE_ERROR_CLASS = "integration.unavailable"
_RETRY_EXHAUSTED_ERROR_CLASS = "integration.retry_budget_exhausted"


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


def _tenant_exists(tenant_id: int | str) -> bool:
    """Return whether the tenant reference exists, resolved lazily via the app registry."""
    from django.apps import apps as django_apps

    tenant_model = django_apps.get_model("organization", "Tenant")
    return tenant_model._base_manager.filter(pk=tenant_id).exists()


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


def _mark_dead_locked(delivery, *, error_class: str, error_message: str, response_code: int | None = None) -> None:
    now = timezone.now()
    delivery.status = "dead"
    delivery.response_code = response_code
    delivery.error_class = error_class
    delivery.error_message = error_message
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
            "updated_at",
        ]
    )


def _reject_mismatched_delivery(delivery, *, webhook_endpoint_id, event_id, tenant_id):
    """Return a terminal result when the locked record contradicts the task identity.

    A replayed or re-targeted task must never deliver under a mismatched identity:
    the record is marked dead (fail closed) and the task short-circuits.
    """
    mismatched = False
    if webhook_endpoint_id is not None and delivery.endpoint_id != webhook_endpoint_id:
        mismatched = True
    if event_id is not None and delivery.event_id is not None and delivery.event_id != event_id:
        mismatched = True
    if tenant_id is not None and delivery.tenant_id != tenant_id:
        mismatched = True
    if not mismatched:
        return None
    _mark_dead_locked(
        delivery,
        error_class=_CONFIGURATION_ERROR_CLASS,
        error_message=_SAFE_CONFIGURATION_MESSAGE,
    )
    return _configuration_result()


def _reject_invalid_delivery(delivery, *, webhook_endpoint_id, endpoint, url):
    """Return a terminal result when the target is missing, disabled, or crossed tenants."""
    invalid = False
    if webhook_endpoint_id is not None and endpoint is None:
        invalid = True
    if endpoint is not None:
        if not endpoint.enabled or (endpoint.tenant_id is not None and delivery.tenant_id != endpoint.tenant_id):
            invalid = True
    if endpoint is None and not url:
        invalid = True
    if not invalid:
        return None
    _mark_dead_locked(
        delivery,
        error_class=_CONFIGURATION_ERROR_CLASS,
        error_message=_SAFE_CONFIGURATION_MESSAGE,
    )
    return _configuration_result()


def _start_delivery(
    *,
    delivery_id: str | None,
    webhook_endpoint_id: int | None,
    event_id: int | str | None,
    tenant_id: int | str | None,
    attempt: int,
    test_send: bool,
    url: str,
    retry_count: int,
):
    # inline import: app-registry: worker-side delivery rows and endpoint validation are lazy
    from extras.models import Event, WebhookDelivery, WebhookEndpoint

    stable_delivery_id = delivery_id or str(uuid4())
    with transaction.atomic():
        delivery = WebhookDelivery._base_manager.select_for_update().filter(delivery_id=stable_delivery_id).first()
        if delivery is not None:
            if delivery.status in ("success", "dead"):
                return delivery, None, False

            reject = _reject_mismatched_delivery(
                delivery,
                webhook_endpoint_id=webhook_endpoint_id,
                event_id=event_id,
                tenant_id=tenant_id,
            )
            if reject is not None:
                return delivery, reject, False

            effective_endpoint_id = delivery.endpoint_id
            endpoint = (
                WebhookEndpoint._base_manager.filter(pk=effective_endpoint_id, deleted_at__isnull=True).first()
                if effective_endpoint_id is not None
                else None
            )
        else:
            endpoint = (
                WebhookEndpoint._base_manager.filter(pk=webhook_endpoint_id, deleted_at__isnull=True).first()
                if webhook_endpoint_id is not None
                else None
            )
            effective_endpoint_id = endpoint.pk if endpoint is not None else None
            effective_tenant_id = tenant_id
            if effective_tenant_id is None and endpoint is not None:
                effective_tenant_id = endpoint.tenant_id
            # Fail closed on unknown tenant references: a stale or tampered task
            # payload must never crash the worker on a foreign-key violation.
            if effective_tenant_id is not None and not _tenant_exists(effective_tenant_id):
                effective_tenant_id = None
            event_link_id = (
                event_id if event_id is not None and Event._base_manager.filter(pk=event_id).exists() else None
            )
            delivery = WebhookDelivery._base_manager.create(
                tenant_id=effective_tenant_id,
                endpoint_id=effective_endpoint_id,
                event_id=event_link_id,
                delivery_id=stable_delivery_id,
                status="pending",
                attempt=1,
                test_send=test_send,
            )

        reject = _reject_invalid_delivery(
            delivery,
            webhook_endpoint_id=webhook_endpoint_id,
            endpoint=endpoint,
            url=url,
        )
        if reject is not None:
            return delivery, reject, False

        delivery.attempt = max(delivery.attempt, attempt + 1)
        delivery.attempted_at = timezone.now()
        delivery.next_retry_at = None
        delivery.save(update_fields=["attempt", "attempted_at", "next_retry_at", "updated_at"])
        return delivery, endpoint, True


def _retry_kwargs(
    *,
    url: str,
    method: str,
    headers: Mapping[str, str],
    secret: str | None,
    webhook_endpoint_id: int | None,
    event_id: int | str | None,
    delivery,
    tenant_id: int | str | None,
    event_action: str,
    event_model_app_label: str,
    event_model_name: str,
    event_object_id: int | str,
    event_timestamp_iso: str,
    event_data: Mapping[str, object],
    retry_count: int,
    retry_backoff: int,
    actor_id: int | None,
    request_id: str | None,
) -> dict[str, object]:
    return dict(
        url=url,
        method=method,
        headers=headers,
        secret="" if webhook_endpoint_id else secret,
        webhook_endpoint_id=webhook_endpoint_id,
        event_id=event_id,
        delivery_id=delivery.delivery_id,
        tenant_id=tenant_id,
        event_action=event_action,
        event_model_app_label=event_model_app_label,
        event_model_name=event_model_name,
        event_object_id=event_object_id,
        event_timestamp_iso=event_timestamp_iso,
        event_data=event_data,
        attempt=delivery.attempt,
        retry_count=retry_count,
        retry_backoff=retry_backoff,
        actor_id=actor_id,
        request_id=request_id,
        test_send=delivery.test_send,
    )


def _finish_delivery(
    *,
    delivery_id: str,
    result: DeliveryResult,
    response_code: int | None,
    retry_count: int,
    retry_backoff: int,
    retry_kwargs: dict[str, object] | None,
) -> DeliveryResult:
    # inline import: app-registry: finalization only needs the delivery model after task startup
    from extras.models import WebhookDelivery

    immediate_retry = False
    with transaction.atomic():
        delivery = WebhookDelivery._base_manager.select_for_update().get(delivery_id=delivery_id)
        if delivery.status in ("success", "dead"):
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
                    "updated_at",
                ]
            )
            if retry_kwargs is not None:
                Schedule.objects.create(
                    func="core.tasks.send_webhook_task",
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
                    "updated_at",
                ]
            )
            immediate_retry = retry_kwargs is not None

    if immediate_retry:
        async_task("core.tasks.send_webhook_task", **retry_kwargs)
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
    url: str,
    method: str,
    headers: Mapping[str, str],
    secret: str | None,
    event_action: str,
    event_model_app_label: str,
    event_model_name: str,
    event_object_id: int | str,
    event_timestamp_iso: str,
    event_data: Mapping[str, object],
    attempt: int = 0,
    retry_count: int = 3,
    retry_backoff: int = 60,
    webhook_endpoint_id: int | None = None,
    event_id: int | str | None = None,
    delivery_id: str | None = None,
    tenant_id: int | str | None = None,
    actor_id: int | None = None,
    request_id: str | None = None,
    *,
    test_send: bool = False,
) -> DeliveryResult:
    """Dispatch a webhook event and persist its durable delivery state."""
    delivery, endpoint_or_result, should_send = _start_delivery(
        delivery_id=delivery_id,
        webhook_endpoint_id=webhook_endpoint_id,
        event_id=event_id,
        tenant_id=tenant_id,
        attempt=attempt,
        test_send=test_send,
        url=url,
        retry_count=retry_count,
    )
    if not should_send:
        if isinstance(endpoint_or_result, DeliveryResult):
            return endpoint_or_result
        return DeliveryResult("webhook.deliver", DeliveryDisposition.NOOP)

    endpoint = endpoint_or_result
    if endpoint is not None:
        url = endpoint.url
        method = endpoint.http_method
        secret = endpoint.secret_decrypted
        retry_count = endpoint.retry_count
        retry_backoff = endpoint.retry_backoff
        headers = headers or endpoint.headers or {}

    # inline import: heavy-import: core.http imports core.validators; defer it off the task module import path.
    from core.http import webhook_target_kind

    operation = "webhook.deliver"
    context = delivery_log_context(
        operation,
        tenant_id=delivery.tenant_id,
        actor_id=actor_id,
        request_id=request_id,
        endpoint=url,
    )
    response_code = None
    try:
        target_kind = webhook_target_kind(url)
        envelope = _webhook_envelope(
            event_id=event_id,
            delivery_id=delivery.delivery_id,
            attempt=delivery.attempt - 1,
            tenant_id=delivery.tenant_id,
        )
        summary = f"Event: {event_action} on {event_model_name} (ID: {event_object_id})"
        test_fields = {
            "event": "test",
            "model": "extras.WebhookEndpoint",
            "object_id": event_object_id,
            "timestamp": event_timestamp_iso,
            "data": {},
        }
        response = _dispatch_webhook_request(
            target_kind=target_kind,
            url=url,
            method=method,
            headers=headers,
            secret=secret,
            envelope=envelope,
            summary=summary,
            test_send=delivery.test_send,
            test_fields=test_fields,
            event_action=event_action,
            event_model_app_label=event_model_app_label,
            event_model_name=event_model_name,
            event_object_id=event_object_id,
            event_timestamp_iso=event_timestamp_iso,
            event_data=event_data,
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
                delivery_id=delivery.delivery_id,
                result=result,
                response_code=response_code,
                retry_count=retry_count,
                retry_backoff=retry_backoff,
                retry_kwargs=None,
            )
        response.raise_for_status()
        logger.info("%s disposition=success", delivery_log_message(context))
        return _finish_delivery(
            delivery_id=delivery.delivery_id,
            result=DeliveryResult(operation, DeliveryDisposition.SUCCESS),
            response_code=response_code,
            retry_count=retry_count,
            retry_backoff=retry_backoff,
            retry_kwargs=None,
        )

    except ValidationError:
        # Blocked by the SSRF guard (internal target, bad scheme, or unresolvable
        # host — fail closed). Final: never retried.
        logger.error("%s disposition=terminal reason=invalid_target", delivery_log_message(context))
        result = _configuration_result()
        return _finish_delivery(
            delivery_id=delivery.delivery_id,
            result=result,
            response_code=response_code,
            retry_count=retry_count,
            retry_backoff=retry_backoff,
            retry_kwargs=None,
        )
    except requests.RequestException as exc:
        exception_response = getattr(exc, "response", None)
        response_code = response_code or _safe_response_code(exception_response)
        if attempt >= retry_count:
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
                retry_count,
            )
            result = DeliveryResult(
                operation,
                DeliveryDisposition.RETRYABLE,
                error_class=_UNAVAILABLE_ERROR_CLASS,
            )
            retry_kwargs = _retry_kwargs(
                url=url,
                method=method,
                headers=headers,
                secret=secret,
                webhook_endpoint_id=delivery.endpoint_id,
                event_id=event_id,
                delivery=delivery,
                tenant_id=delivery.tenant_id,
                event_action=event_action,
                event_model_app_label=event_model_app_label,
                event_model_name=event_model_name,
                event_object_id=event_object_id,
                event_timestamp_iso=event_timestamp_iso,
                event_data=event_data,
                retry_count=retry_count,
                retry_backoff=retry_backoff,
                actor_id=actor_id,
                request_id=request_id,
            )
        return _finish_delivery(
            delivery_id=delivery.delivery_id,
            result=result,
            response_code=response_code,
            retry_count=retry_count,
            retry_backoff=retry_backoff,
            retry_kwargs=retry_kwargs,
        )


def _load_delivery_for_actor(delivery_pk: int, actor_id: int | None):
    # inline import: app-registry: manual operations resolve operational models lazily in workers.
    from extras.models import WebhookDelivery

    actor = _actor_for_id(actor_id)
    if actor is None:
        raise PermissionDenied("Delivery not found.")
    delivery = (
        WebhookDelivery.objects.visible_to(actor).select_related("endpoint", "event").filter(pk=delivery_pk).first()
    )
    if delivery is None:
        raise PermissionDenied("Delivery not found.")
    return delivery, actor


def _legacy_config_for_delivery(delivery):
    # inline import: app-registry: legacy rule lookup is only needed for redelivery of pre-endpoint records.
    from extras.models import EventRule

    if delivery.event is None:
        return {}
    rules = (
        EventRule._base_manager.filter(action_type=EventRule.ACTION_WEBHOOK, webhook__isnull=True)
        .filter(Q(tenant_id=delivery.tenant_id) | Q(tenant__isnull=True))
        .order_by("pk")
    )
    for rule in rules:
        if delivery.event.action in (rule.events or []):
            config = rule.action_config or {}
            if config.get("url"):
                return config
    return {}


def _redelivery_task_kwargs(delivery, *, actor_id: int | None) -> dict[str, object]:
    endpoint = delivery.endpoint
    event = delivery.event
    now = timezone.now()
    if delivery.test_send:
        event_id = None
        event_action = "test"
        event_model_app_label = "extras"
        event_model_name = "WebhookEndpoint"
        event_object_id = endpoint.pk if endpoint is not None else ""
        event_timestamp_iso = now.isoformat()
        event_data = {}
    elif event is not None:
        event_id = event.pk
        event_action = event.action
        event_model_app_label = event.model.app_label
        event_model_name = event.model.model
        event_object_id = event.object_id
        event_timestamp_iso = event.timestamp.isoformat()
        event_data = event.data
    else:
        raise ValidationError(_SAFE_CONFIGURATION_MESSAGE)

    if endpoint is not None:
        return dict(
            url=endpoint.url,
            method=endpoint.http_method,
            headers=endpoint.headers or {},
            secret="",
            webhook_endpoint_id=endpoint.pk,
            event_id=event_id,
            delivery_id=str(uuid4()),
            tenant_id=delivery.tenant_id,
            event_action=event_action,
            event_model_app_label=event_model_app_label,
            event_model_name=event_model_name,
            event_object_id=event_object_id,
            event_timestamp_iso=event_timestamp_iso,
            event_data=event_data,
            retry_count=endpoint.retry_count,
            retry_backoff=endpoint.retry_backoff,
            actor_id=actor_id,
            request_id=None,
            test_send=delivery.test_send,
        )

    config = _legacy_config_for_delivery(delivery)
    url = config.get("url", "")
    method = (config.get("method") or "POST").upper()
    headers = config.get("headers") or {}
    secret = config.get("secret", "")
    return dict(
        url=url,
        method=method,
        headers=headers,
        secret=secret,
        webhook_endpoint_id=None,
        event_id=event_id,
        delivery_id=str(uuid4()),
        tenant_id=delivery.tenant_id,
        event_action=event_action,
        event_model_app_label=event_model_app_label,
        event_model_name=event_model_name,
        event_object_id=event_object_id,
        event_timestamp_iso=event_timestamp_iso,
        event_data=event_data,
        retry_count=3,
        retry_backoff=60,
        actor_id=actor_id,
        request_id=None,
        test_send=False,
    )


def _enqueue_task(task_kwargs: dict[str, object]) -> None:
    if getattr(settings, "Q_CLUSTER", {}).get("sync", False):
        async_task("core.tasks.send_webhook_task", **task_kwargs)
    else:
        transaction.on_commit(lambda task_kwargs=task_kwargs: async_task("core.tasks.send_webhook_task", **task_kwargs))


def redeliver_webhook_delivery(delivery_pk: int, *, actor_id: int | None = None):
    """Create and enqueue a fresh delivery for an existing delivery outcome."""
    # inline import: app-registry: creation of the redelivery row stays lazy for django-q loading.
    from extras.models import WebhookDelivery

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

        task_kwargs = _redelivery_task_kwargs(source, actor_id=actor_id)
        new_delivery = WebhookDelivery._base_manager.create(
            tenant_id=source.tenant_id,
            endpoint_id=source.endpoint_id,
            event_id=source.event_id,
            delivery_id=task_kwargs["delivery_id"],
            status="pending",
            test_send=source.test_send,
            redelivered_from=source,
            redelivered_by=actor,
            redelivered_at=now,
        )
        _enqueue_task(task_kwargs)
    return new_delivery


def send_webhook_test(endpoint_pk: int, *, actor_id: int | None = None):
    """Create and enqueue a test delivery for an endpoint."""
    # inline import: app-registry: endpoint and delivery models are resolved only for this operation.
    from extras.models import WebhookDelivery, WebhookEndpoint

    actor = _actor_for_id(actor_id)
    if actor is not None and _is_platform_actor(actor):
        endpoint = WebhookEndpoint._base_manager.filter(pk=endpoint_pk, deleted_at__isnull=True).first()
    else:
        endpoint = WebhookEndpoint.objects.filter(pk=endpoint_pk).first()
    if endpoint is None or (endpoint.tenant_id is None and not _is_platform_actor(actor)):
        raise PermissionDenied("Webhook endpoint not found.")

    now = timezone.now()
    delivery_id = str(uuid4())
    task_kwargs = dict(
        url=endpoint.url,
        method=endpoint.http_method,
        headers=endpoint.headers or {},
        secret="",
        webhook_endpoint_id=endpoint.pk,
        event_id=None,
        delivery_id=delivery_id,
        tenant_id=endpoint.tenant_id,
        event_action="test",
        event_model_app_label="extras",
        event_model_name="WebhookEndpoint",
        event_object_id=endpoint.pk,
        event_timestamp_iso=now.isoformat(),
        event_data={},
        retry_count=endpoint.retry_count,
        retry_backoff=endpoint.retry_backoff,
        actor_id=actor_id,
        request_id=None,
        test_send=True,
    )
    with transaction.atomic():
        delivery = WebhookDelivery._base_manager.create(
            tenant_id=endpoint.tenant_id,
            endpoint=endpoint,
            event=None,
            delivery_id=delivery_id,
            status="pending",
            test_send=True,
        )
        _enqueue_task(task_kwargs)
    return delivery
