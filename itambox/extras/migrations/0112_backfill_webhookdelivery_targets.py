"""Backfill immutable target snapshots for endpoint-backed durable deliveries.

Endpoint-less historical rows cannot be bound to an exact rule after the fact,
so they deliberately remain without a target and fail closed if dispatched.
"""

from django.db import migrations

TARGET_FIELDS = (
    "payload_timestamp",
    "target_url",
    "target_http_method",
    "target_headers",
    "target_secret",
    "target_enabled",
    "target_tenant_id",
    "target_retry_count",
    "target_retry_backoff",
)


def forward(apps, schema_editor):
    from core.crypto import encrypt_string

    Delivery = apps.get_model("extras", "WebhookDelivery")
    db_alias = schema_editor.connection.alias
    batch = []
    deliveries = (
        Delivery.objects.using(db_alias)
        .filter(endpoint_id__isnull=False)
        .select_related("endpoint", "event")
        .iterator(chunk_size=500)
    )
    for delivery in deliveries:
        endpoint = delivery.endpoint
        delivery.payload_timestamp = delivery.event.timestamp if delivery.event_id else delivery.created_at
        delivery.target_url = endpoint.url
        delivery.target_http_method = (endpoint.http_method or "POST").upper()
        delivery.target_headers = endpoint.headers or {}
        secret = endpoint.secret or ""
        delivery.target_secret = secret if not secret or secret.startswith("enc$") else encrypt_string(secret)
        delivery.target_enabled = endpoint.enabled and endpoint.deleted_at is None
        delivery.target_tenant_id = endpoint.tenant_id
        delivery.target_retry_count = endpoint.retry_count
        delivery.target_retry_backoff = endpoint.retry_backoff
        batch.append(delivery)
        if len(batch) == 500:
            Delivery.objects.using(db_alias).bulk_update(batch, TARGET_FIELDS)
            batch.clear()
    if batch:
        Delivery.objects.using(db_alias).bulk_update(batch, TARGET_FIELDS)


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0111_webhookdelivery_target_claim"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
