"""Upgrade legacy webhook retry schedules to durable assertion payloads.

Issue #445 deliberately changed the webhook worker from a target/payload kwargs
surface to immutable ``WebhookDeliveryAssertions``. Delayed one-shot retry
schedules are not consumed by the scheduler-disabled drain worker, so their
payload must be upgraded before scheduler unfreeze. This migration also moves
endpoint-less legacy target data out of plaintext ``Schedule.kwargs`` and into
the encrypted durable delivery snapshot.

Malformed or ambiguous rows fail the migration with a stable, secret-free code.
The operation is intentionally upgrade-only; rollback uses the restore-first
procedure and never reintroduces plaintext queue payloads.
"""

import ast
from uuid import UUID

from django.db import migrations
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

OLD_FUNC = "extras.tasks.webhooks.send_webhook_task"
LEGACY_KEYS = frozenset(
    {
        "url",
        "method",
        "headers",
        "secret",
        "webhook_endpoint_id",
        "event_id",
        "delivery_id",
        "tenant_id",
        "event_action",
        "event_model_app_label",
        "event_model_name",
        "event_object_id",
        "event_timestamp_iso",
        "event_data",
        "attempt",
        "retry_count",
        "retry_backoff",
        "actor_id",
        "request_id",
        "test_send",
    }
)
ASSERTION_KEYS = frozenset(
    {
        "delivery_pk",
        "delivery_id",
        "webhook_endpoint_id",
        "event_id",
        "tenant_id",
        "test_send",
    }
)
NEW_KEYS = frozenset({"assertions", "attempt", "actor_id", "request_id"})
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH"})


def _fail(code):
    raise RuntimeError(f"issue445.webhook_retry_upgrade.{code}")


def _parse_kwargs(value):
    if not isinstance(value, str):
        _fail("invalid_literal")
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, TypeError, ValueError):
        _fail("invalid_literal")
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        _fail("invalid_mapping")
    return parsed


def _valid_new_payload(payload):
    assertions = payload.get("assertions")
    if set(payload) != NEW_KEYS or not isinstance(assertions, dict) or set(assertions) != ASSERTION_KEYS:
        return False
    try:
        UUID(str(assertions["delivery_id"]))
    except (TypeError, ValueError, AttributeError):
        return False
    return (
        isinstance(assertions["delivery_pk"], int)
        and not isinstance(assertions["delivery_pk"], bool)
        and (assertions["webhook_endpoint_id"] is None or isinstance(assertions["webhook_endpoint_id"], int))
        and (assertions["event_id"] is None or isinstance(assertions["event_id"], int))
        and (assertions["tenant_id"] is None or isinstance(assertions["tenant_id"], int))
        and isinstance(assertions["test_send"], bool)
        and isinstance(payload["attempt"], int)
        and payload["attempt"] >= 0
        and (payload["actor_id"] is None or isinstance(payload["actor_id"], int))
        and (payload["request_id"] is None or isinstance(payload["request_id"], str))
    )


def _legacy_target(payload, delivery):
    if set(payload) != LEGACY_KEYS:
        _fail("invalid_legacy_shape")
    if str(payload["delivery_id"]) != str(delivery.delivery_id):
        _fail("delivery_identity_mismatch")

    url = payload["url"]
    method = payload["method"]
    headers = payload["headers"]
    secret = payload["secret"] or ""
    retry_count = payload["retry_count"]
    retry_backoff = payload["retry_backoff"]
    attempt = payload["attempt"]
    timestamp = (
        parse_datetime(payload["event_timestamp_iso"]) if isinstance(payload["event_timestamp_iso"], str) else None
    )
    if not isinstance(url, str) or not url or not isinstance(method, str):
        _fail("invalid_target")
    method = method.upper()
    if method not in ALLOWED_METHODS:
        _fail("invalid_method")
    if not isinstance(headers, dict) or any(not isinstance(key, str) for key in headers):
        _fail("invalid_headers")
    if not isinstance(secret, str):
        _fail("invalid_secret")
    if timestamp is None or not is_aware(timestamp):
        _fail("invalid_timestamp")
    for value in (retry_count, retry_backoff, attempt):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _fail("invalid_retry_state")
    if retry_count > 32767 or retry_backoff > 32767:
        _fail("invalid_retry_state")
    return url, method, headers, secret, retry_count, retry_backoff, attempt, timestamp


def forward(apps, schema_editor):
    from core.crypto import encrypt_string

    Schedule = apps.get_model("django_q", "Schedule")
    Delivery = apps.get_model("extras", "WebhookDelivery")
    db_alias = schema_editor.connection.alias
    schedules = Schedule.objects.using(db_alias).filter(func=OLD_FUNC).order_by("pk")

    for schedule in schedules.iterator(chunk_size=200):
        payload = _parse_kwargs(schedule.kwargs)
        if _valid_new_payload(payload):
            continue
        delivery_id = payload.get("delivery_id")
        if delivery_id is None:
            _fail("delivery_identity_missing")
        delivery = Delivery.objects.using(db_alias).filter(delivery_id=str(delivery_id)).first()
        if delivery is None:
            _fail("delivery_unknown")
        url, method, headers, secret, retry_count, retry_backoff, attempt, timestamp = _legacy_target(payload, delivery)

        endpoint_id = payload["webhook_endpoint_id"]
        if endpoint_id != delivery.endpoint_id:
            _fail("endpoint_identity_mismatch")
        if delivery.event_id != payload["event_id"] or delivery.tenant_id != payload["tenant_id"]:
            _fail("relation_identity_mismatch")
        if delivery.test_send is not payload["test_send"]:
            _fail("test_send_identity_mismatch")

        update_fields = ["payload_timestamp"]
        delivery.payload_timestamp = timestamp
        # 0112 already snapshots endpoint-backed targets. Endpoint-less retry
        # schedules are the only trustworthy source for their exact historical
        # target, so move it into the durable row and scrub the schedule below.
        if endpoint_id is None:
            delivery.target_url = url
            delivery.target_http_method = method
            delivery.target_headers = headers
            delivery.target_secret = secret if not secret or secret.startswith("enc$") else encrypt_string(secret)
            delivery.target_enabled = True
            delivery.target_tenant_id = delivery.tenant_id
            delivery.target_retry_count = retry_count
            delivery.target_retry_backoff = retry_backoff
            update_fields.extend(
                [
                    "target_url",
                    "target_http_method",
                    "target_headers",
                    "target_secret",
                    "target_enabled",
                    "target_tenant_id",
                    "target_retry_count",
                    "target_retry_backoff",
                ]
            )
        elif not delivery.target_url:
            _fail("endpoint_snapshot_missing")
        delivery.save(using=db_alias, update_fields=update_fields)

        assertions = {
            "delivery_pk": delivery.pk,
            "delivery_id": str(delivery.delivery_id),
            "webhook_endpoint_id": delivery.endpoint_id,
            "event_id": delivery.event_id,
            "tenant_id": delivery.tenant_id,
            "test_send": delivery.test_send,
        }
        schedule.kwargs = repr(
            {
                "assertions": assertions,
                "attempt": attempt,
                "actor_id": payload["actor_id"],
                "request_id": payload["request_id"],
            }
        )
        schedule.save(using=db_alias, update_fields=["kwargs"])


def reverse(apps, schema_editor):
    _fail("reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0112_backfill_webhookdelivery_targets"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [migrations.RunPython(forward, reverse)]
