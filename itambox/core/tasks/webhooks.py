import datetime
import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from uuid import uuid4

import requests
from django.utils import timezone
from django_q.models import Schedule
from django_q.tasks import async_task

from core.events import DeliveryDisposition, DeliveryResult, delivery_log_context, delivery_log_message

logger = logging.getLogger(__name__)
WEBHOOK_ENVELOPE_SCHEMA_VERSION = 1


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
) -> DeliveryResult:
    """Dispatch a webhook event. Retries on 5xx and connection errors; 4xx are final."""
    from django.core.exceptions import ValidationError

    # inline import: heavy-import: core.http imports core.validators (django-loaded); keep the task
    # module import-light for django-q payload loading.
    from core.http import request_pinned, webhook_target_kind

    # Re-derive the (encrypted-at-rest) secret from the endpoint at run time so it never has
    # to be persisted in the django_q payload / retry Schedule.kwargs (both stored plaintext).
    if webhook_endpoint_id and not secret:
        from extras.models import WebhookEndpoint

        endpoint = WebhookEndpoint.all_objects.filter(pk=webhook_endpoint_id).first()
        if endpoint:
            secret = endpoint.secret_decrypted

    # The initial enqueue creates this once; retry kwargs carry it forward. A
    # direct task invocation without an id still gets a valid envelope, but
    # cannot provide durable deduplication until the delivery record work lands.
    delivery_id = delivery_id or str(uuid4())

    # SSRF guard: every send goes through core.http.request_pinned, which
    # validates the URL at send time (fail closed, incl. unresolvable hosts)
    # AND pins the connection to the validated address — the request cannot be
    # re-routed by a second DNS answer between check and use (DNS rebinding),
    # and redirects are never followed. A blocked URL is final — do not retry.
    operation = "webhook.deliver"
    context = delivery_log_context(
        operation,
        tenant_id=tenant_id,
        actor_id=actor_id,
        request_id=request_id,
        endpoint=url,
    )
    target_kind = webhook_target_kind(url)
    try:
        envelope = _webhook_envelope(
            event_id=event_id,
            delivery_id=delivery_id,
            attempt=attempt,
            tenant_id=tenant_id,
        )
        summary = f"Event: {event_action} on {event_model_name} (ID: {event_object_id})"
        if target_kind == "slack":
            payload = {**envelope, "text": summary}
            response = request_pinned("POST", url, json=payload, timeout=10)
        elif target_kind == "teams":
            payload = {
                **envelope,
                "@type": "MessageCard",
                "@context": "https://schema.org/extensions",
                "summary": summary,
                "themeColor": "0076D7",
                "title": "ITAMbox Notification",
                "text": summary,
            }
            response = request_pinned("POST", url, json=payload, timeout=10)
        else:
            payload = {
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
            response = request_pinned(method, url, headers=req_headers, data=body, timeout=10)

        if 400 <= response.status_code < 500:
            logger.warning("%s disposition=terminal reason=http_4xx", delivery_log_message(context))
            return DeliveryResult(operation, DeliveryDisposition.TERMINAL, True, "Webhook delivery was rejected.")
        response.raise_for_status()
        logger.info("%s disposition=success", delivery_log_message(context))
        return DeliveryResult(operation, DeliveryDisposition.SUCCESS)

    except ValidationError:
        # Blocked by the SSRF guard (internal target, bad scheme, or unresolvable
        # host — fail closed). Final: never retried.
        logger.error("%s disposition=terminal reason=invalid_target", delivery_log_message(context))
        return DeliveryResult(operation, DeliveryDisposition.TERMINAL, True, "Invalid webhook configuration.")
    except requests.RequestException:
        if attempt >= retry_count:
            logger.error("%s disposition=retryable reason=attempt_limit", delivery_log_message(context))
            return DeliveryResult(operation, DeliveryDisposition.RETRYABLE)

        retry_kwargs = dict(
            url=url,
            method=method,
            headers=headers,
            # Endpoint-linked retries carry only the endpoint pk so the secret is never
            # written to Schedule.kwargs (re-derived on the next run); legacy webhooks keep
            # their plaintext config secret.
            secret="" if webhook_endpoint_id else secret,
            webhook_endpoint_id=webhook_endpoint_id,
            event_id=event_id,
            delivery_id=delivery_id,
            tenant_id=tenant_id,
            event_action=event_action,
            event_model_app_label=event_model_app_label,
            event_model_name=event_model_name,
            event_object_id=event_object_id,
            event_timestamp_iso=event_timestamp_iso,
            event_data=event_data,
            attempt=attempt + 1,
            retry_count=retry_count,
            retry_backoff=retry_backoff,
            actor_id=actor_id,
            request_id=request_id,
        )

        if retry_backoff and retry_backoff > 0:
            # django-q2's async_task has no native delay, so honour retry_backoff
            # with a one-off Schedule row that the qcluster beat dispatches at
            # next_run. Schedule.kwargs is parsed back with ast.literal_eval, so it
            # must be a Python-literal repr — safe here, as every value is a string,
            # int, or a JSONField-sourced dict of primitives. repeats defaults to
            # -1, which makes django-q delete the schedule after it fires once.
            logger.warning(
                "%s disposition=retryable action=scheduled attempt=%d retry_count=%d delay_seconds=%d",
                delivery_log_message(context),
                attempt + 1,
                retry_count,
                retry_backoff,
            )
            Schedule.objects.create(
                func="core.tasks.send_webhook_task",
                kwargs=repr(retry_kwargs),
                schedule_type=Schedule.ONCE,
                next_run=timezone.now() + datetime.timedelta(seconds=retry_backoff),
            )
        else:
            logger.warning(
                "%s disposition=retryable action=reenqueued attempt=%d retry_count=%d",
                delivery_log_message(context),
                attempt + 1,
                retry_count,
            )
            async_task("core.tasks.send_webhook_task", **retry_kwargs)
        return DeliveryResult(operation, DeliveryDisposition.RETRYABLE)
