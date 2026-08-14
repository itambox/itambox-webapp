from django.db import migrations, models


def _target_tenant_id(target):
    tenant_id = getattr(target, "tenant_id", None)
    if tenant_id is not None:
        return tenant_id
    tenant = getattr(target, "tenant", None)
    return getattr(tenant, "pk", None)


def reconcile_alert_tenants(apps, schema_editor):
    AlertLog = apps.get_model("extras", "AlertLog")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for alert in AlertLog._base_manager.filter(tenant__isnull=True).iterator():
        target = None
        try:
            content_type = ContentType.objects.get(pk=alert.content_type_id)
            model = apps.get_model(content_type.app_label, content_type.model)
            if model is not None:
                target = model._base_manager.filter(pk=alert.object_id).first()
        except (ContentType.DoesNotExist, LookupError, AttributeError):
            target = None

        tenant_id = _target_tenant_id(target) if target is not None else None
        if tenant_id is not None:
            conflict = (
                alert.status in ["active", "acknowledged"]
                and AlertLog._base_manager.filter(
                    rule_id=alert.rule_id,
                    content_type_id=alert.content_type_id,
                    object_id=alert.object_id,
                    status__in=["active", "acknowledged"],
                )
                .exclude(pk=alert.pk)
                .exists()
            )
            if conflict:
                AlertLog._base_manager.filter(pk=alert.pk).update(tenant_resolution_status="unresolved")
            else:
                AlertLog._base_manager.filter(pk=alert.pk).update(
                    tenant_id=tenant_id,
                    tenant_resolution_status="resolved",
                )
        elif target is not None:
            AlertLog._base_manager.filter(pk=alert.pk).update(tenant_resolution_status="global")
        else:
            AlertLog._base_manager.filter(pk=alert.pk).update(tenant_resolution_status="unresolved")


def reverse_alert_tenant_reconciliation(apps, schema_editor):
    # Preservation-only reverse: the field is removed immediately afterwards,
    # while tenant attribution and alert content intentionally survive rollback.
    return None


class Migration(migrations.Migration):
    # PostgreSQL change-log triggers queue events for the backfill UPDATE. Keep
    # the AddField/index DDL and the data operation in separate transactions so
    # deferred CREATE INDEX cannot run while AlertLog still has pending trigger
    # events during a real MigrationExecutor forward migration.
    atomic = False

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("extras", "0103_remove_reporttemplate_advanced_mode_and_more"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AddField(
            model_name="alertlog",
            name="tenant_resolution_status",
            field=models.CharField(
                choices=[
                    ("not_required", "Not required"),
                    ("resolved", "Resolved from target"),
                    ("global", "Global target"),
                    ("unresolved", "Unresolved — operator review required"),
                ],
                db_index=True,
                default="not_required",
                help_text="Reconciliation state for legacy tenant-less alerts.",
                max_length=20,
                verbose_name="Tenant Resolution",
            ),
        ),
        migrations.RunPython(reconcile_alert_tenants, reverse_alert_tenant_reconciliation),
    ]
