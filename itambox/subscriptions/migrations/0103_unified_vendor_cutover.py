"""Move subscription providers into the shared supplier catalogue.

When names match within a scope, live suppliers are preferred over tombstones;
a live provider creates a fresh supplier when only a tombstone matches. An
available provider slug is retained; collisions use slugify(name) and then
numeric suffixes, checked against live rows in the applicable unique scope.
Provider admin_notes become Supplier notes on newly created rows. Matched
suppliers receive portal_url and account_id only when their fields are empty.
Provider deleted_at values are preserved on newly created suppliers. This data
cutover is one-way: rollback requires restoring from backup.

Per-object references follow the merge: journal entries (with their
denormalised tenant kept in step), bookmarks, watches, attachments, alert
logs, and queued events are repointed to the matched supplier; bookmark,
watch, and open-alert duplicates that would violate their uniqueness
constraints on the supplier are dropped. Changelog history rows stay behind
as historical records of the removed provider content type.
"""

from django.db import migrations, models
import django.db.models.deletion
from django.utils.text import slugify


def _scope_slug_queryset(Supplier, provider, slug):
    suppliers = Supplier.objects.filter(deleted_at__isnull=True, slug=slug)
    if provider.tenant_id is not None:
        return suppliers.filter(tenant_id=provider.tenant_id, tenant_group_id__isnull=True)
    if provider.tenant_group_id is not None:
        return suppliers.filter(tenant_group_id=provider.tenant_group_id, tenant_id__isnull=True)
    return suppliers.filter(tenant__isnull=True, tenant_group__isnull=True)


def _available_slug(Supplier, provider):
    preferred = provider.slug
    if preferred and not _scope_slug_queryset(Supplier, provider, preferred).exists():
        return preferred

    base_slug = slugify(provider.name)
    candidate = base_slug
    suffix = 2
    while _scope_slug_queryset(Supplier, provider, candidate).exists():
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def _supplier_name_index(Supplier):
    """Index live and deleted suppliers in stable primary-key order."""
    suppliers_by_name = {}
    for supplier in Supplier.objects.all().order_by("pk"):
        key = (supplier.tenant_id, supplier.tenant_group_id, supplier.name.casefold())
        suppliers_by_name.setdefault(key, []).append(supplier)
    return suppliers_by_name


def _create_supplier(Supplier, provider):
    return Supplier.objects.create(
        name=provider.name,
        slug=_available_slug(Supplier, provider),
        website="",
        notes=provider.admin_notes,
        portal_url=provider.portal_url,
        account_id=provider.account_id,
        is_active=provider.is_active,
        tenant_id=provider.tenant_id,
        tenant_group_id=provider.tenant_group_id,
        deleted_at=provider.deleted_at,
    )


def _matched_supplier(matches, provider):
    """Return the preferred existing match, or None when this row needs a new one."""
    live_match = next((match for match in matches if match.deleted_at is None), None)
    if live_match is not None:
        return live_match
    if provider.deleted_at is not None:
        return next((match for match in matches if match.deleted_at is not None), None)
    return None


def _resolve_supplier(Supplier, provider, suppliers_by_name):
    if provider.supplier_id:
        return Supplier.objects.get(pk=provider.supplier_id), False

    key = (provider.tenant_id, provider.tenant_group_id, provider.name.casefold())
    matches = suppliers_by_name.get(key, [])
    match = _matched_supplier(matches, provider)
    if match is not None:
        return match, False

    supplier = _create_supplier(Supplier, provider)
    suppliers_by_name.setdefault(key, []).append(supplier)
    return supplier, True


def _fill_empty_commercial_fields(Supplier, provider, supplier):
    changed = {}
    if supplier.portal_url == "" and provider.portal_url:
        changed["portal_url"] = provider.portal_url
    if supplier.account_id == "" and provider.account_id:
        changed["account_id"] = provider.account_id
    if changed:
        Supplier.objects.filter(pk=supplier.pk).update(**changed)
        for field_name, value in changed.items():
            setattr(supplier, field_name, value)


def _copy_provider_tags(Provider, Supplier, provider_to_supplier):
    # Copy tag links through the historical models' auto-created through tables.
    provider_tags = Provider._meta.get_field("tags")
    supplier_tags = Supplier._meta.get_field("tags")
    provider_through = provider_tags.remote_field.through
    supplier_through = supplier_tags.remote_field.through
    provider_owner_attname = provider_through._meta.get_field(provider_tags.m2m_field_name()).attname
    provider_tag_attname = provider_through._meta.get_field(provider_tags.m2m_reverse_field_name()).attname
    supplier_owner_attname = supplier_through._meta.get_field(supplier_tags.m2m_field_name()).attname
    supplier_tag_attname = supplier_through._meta.get_field(supplier_tags.m2m_reverse_field_name()).attname
    for provider_id, supplier_id in sorted(provider_to_supplier.items()):
        tag_ids = (
            provider_through.objects.filter(**{provider_owner_attname: provider_id})
            .order_by(provider_tag_attname)
            .values_list(provider_tag_attname, flat=True)
        )
        for tag_id in tag_ids:
            supplier_through.objects.get_or_create(
                **{supplier_owner_attname: supplier_id, supplier_tag_attname: tag_id}
            )


def _repoint_provider_contacts(ContactAssignment, ContentType, provider_to_supplier):
    # Repoint generic contact assignments, deduplicating equivalent Supplier rows.
    provider_ct = ContentType.objects.filter(app_label="subscriptions", model="provider").first()
    if provider_ct is None:
        # Fresh installs never materialized a provider content type: nothing to repoint.
        return
    supplier_ct = ContentType.objects.filter(app_label="assets", model="supplier").first()
    if supplier_ct is None:
        supplier_ct = ContentType.objects.create(app_label="assets", model="supplier", name="supplier")
    for assignment in ContactAssignment.objects.filter(content_type_id=provider_ct.pk).order_by("pk"):
        supplier_id = provider_to_supplier.get(assignment.object_id)
        if supplier_id is None:
            raise RuntimeError(
                f"ContactAssignment references a missing subscriptions.Provider row: {assignment.object_id}"
            )
        duplicate = ContactAssignment.objects.filter(
            contact_id=assignment.contact_id,
            role_id=assignment.role_id,
            content_type_id=supplier_ct.pk,
            object_id=supplier_id,
        ).exclude(pk=assignment.pk)
        if duplicate.exists():
            assignment.delete()
        else:
            ContactAssignment.objects.filter(pk=assignment.pk).update(
                content_type_id=supplier_ct.pk,
                object_id=supplier_id,
            )


def _repoint_provider_generics(apps, ContentType, provider_to_supplier, supplier_tenants):
    """Move per-object references from provider rows to the matched suppliers.

    Journal entries keep their denormalised tenant aligned with the supplier.
    Bookmarks and watches carry a unique (user, content type, object) constraint,
    so an equivalent row on the supplier makes the provider-era row a duplicate
    and it is dropped. Open alert logs collide on (rule, content type, object)
    while active/acknowledged; the supplier's existing alert wins. Attachments
    and queued events move unconditionally.
    """
    provider_ct = ContentType.objects.filter(app_label="subscriptions", model="provider").first()
    if provider_ct is None:
        # Fresh installs never materialized a provider content type: nothing to repoint.
        return
    supplier_ct = ContentType.objects.filter(app_label="assets", model="supplier").first()
    if supplier_ct is None:
        supplier_ct = ContentType.objects.create(app_label="assets", model="supplier", name="supplier")

    JournalEntry = apps.get_model("extras", "JournalEntry")
    Bookmark = apps.get_model("extras", "Bookmark")
    ObjectWatch = apps.get_model("extras", "ObjectWatch")
    ImageAttachment = apps.get_model("extras", "ImageAttachment")
    FileAttachment = apps.get_model("extras", "FileAttachment")
    AlertLog = apps.get_model("extras", "AlertLog")
    Event = apps.get_model("extras", "Event")

    def _supplier_id(row):
        supplier_id = provider_to_supplier.get(row.object_id)
        if supplier_id is None:
            raise RuntimeError(f"{type(row).__name__} references a missing subscriptions.Provider row: {row.object_id}")
        return supplier_id

    for entry in JournalEntry.objects.filter(model_id=provider_ct.pk).order_by("pk"):
        supplier_id = _supplier_id(entry)
        JournalEntry.objects.filter(pk=entry.pk).update(
            model_id=supplier_ct.pk,
            object_id=supplier_id,
            tenant_id=supplier_tenants.get(supplier_id),
        )

    for model in (Bookmark, ObjectWatch):
        for row in model.objects.filter(model_id=provider_ct.pk).order_by("pk"):
            supplier_id = _supplier_id(row)
            duplicate = model.objects.filter(
                user_id=row.user_id,
                model_id=supplier_ct.pk,
                object_id=supplier_id,
            ).exclude(pk=row.pk)
            if duplicate.exists():
                row.delete()
            else:
                model.objects.filter(pk=row.pk).update(model_id=supplier_ct.pk, object_id=supplier_id)

    for model in (ImageAttachment, FileAttachment, Event):
        for row in model.objects.filter(model_id=provider_ct.pk).order_by("pk"):
            supplier_id = _supplier_id(row)
            model.objects.filter(pk=row.pk).update(model_id=supplier_ct.pk, object_id=supplier_id)

    for alert in AlertLog.objects.filter(content_type_id=provider_ct.pk).order_by("pk"):
        supplier_id = _supplier_id(alert)
        if alert.status in ("active", "acknowledged"):
            duplicate = AlertLog.objects.filter(
                rule_id=alert.rule_id,
                content_type_id=supplier_ct.pk,
                object_id=supplier_id,
                status__in=["active", "acknowledged"],
            ).exclude(pk=alert.pk)
            if duplicate.exists():
                alert.delete()
                continue
        AlertLog.objects.filter(pk=alert.pk).update(
            content_type_id=supplier_ct.pk,
            object_id=supplier_id,
            tenant_id=supplier_tenants.get(supplier_id),
        )


def forwards(apps, schema_editor):
    Provider = apps.get_model("subscriptions", "Provider")
    Subscription = apps.get_model("subscriptions", "Subscription")
    Supplier = apps.get_model("assets", "Supplier")
    ContactAssignment = apps.get_model("organization", "ContactAssignment")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # Historical managers are plain. Keep every supplier for matching and let
    # _scope_slug_queryset filter tombstones out of uniqueness checks.
    suppliers_by_name = _supplier_name_index(Supplier)
    provider_to_supplier = {}
    for provider in Provider.objects.all().order_by("pk"):
        supplier, created = _resolve_supplier(Supplier, provider, suppliers_by_name)
        if not created:
            _fill_empty_commercial_fields(Supplier, provider, supplier)
        provider_to_supplier[provider.pk] = supplier.pk
        Subscription.objects.filter(provider_id=provider.pk).update(supplier_id=supplier.pk)

    _copy_provider_tags(Provider, Supplier, provider_to_supplier)
    _repoint_provider_contacts(ContactAssignment, ContentType, provider_to_supplier)
    supplier_tenants = dict(
        Supplier.objects.filter(pk__in=provider_to_supplier.values()).values_list("pk", "tenant_id")
    )
    _repoint_provider_generics(apps, ContentType, provider_to_supplier, supplier_tenants)
    _rename_provider_permissions(apps)
    _translate_provider_references(apps, ContentType, provider_to_supplier)


def _rename_provider_permissions(apps):
    """Translate legacy ``subscriptions.*_provider`` grants to their Supplier equivalents.

    Role permissions are persisted as literal "app_label.codename" strings in a
    JSON field, so the policy rename (Provider retired, Supplier in ``assets``)
    only changes what the UI offers — existing custom roles would keep the
    retired strings and silently lose access. Rename in place, preserving
    order; only roles actually carrying a Provider grant are rewritten.
    """
    Role = apps.get_model("organization", "Role")
    legacy_map = {
        "subscriptions.view_provider": "assets.view_supplier",
        "subscriptions.add_provider": "assets.add_supplier",
        "subscriptions.change_provider": "assets.change_supplier",
        "subscriptions.delete_provider": "assets.delete_supplier",
    }
    for role in Role.objects.all().only("pk", "permissions").iterator():
        permissions = list(role.permissions or [])
        renamed = [legacy_map.get(permission, permission) for permission in permissions]
        if renamed == permissions:
            continue
        role.permissions = list(dict.fromkeys(renamed))
        role.save(update_fields=["permissions"])


def _translate_provider_references(apps, ContentType, provider_to_supplier):
    """Repoint durable Provider-bound configuration at the Supplier surfaces.

    Report templates, saved filters, event rules and export templates persist
    column keys or ContentType references to the retired model; without this
    step they silently stop matching after the cutover (rules never fire,
    report columns vanish, filters go inert). Filter parameter values are
    mapped through the provider transplant while the keys are renamed.
    """
    provider_ct = ContentType.objects.filter(app_label="subscriptions", model="provider").first()
    supplier_ct = ContentType.objects.filter(app_label="assets", model="supplier").first()
    if provider_ct is None or supplier_ct is None:
        return

    EventRule = apps.get_model("extras", "EventRule")
    ExportTemplate = apps.get_model("extras", "ExportTemplate")
    SavedFilter = apps.get_model("extras", "SavedFilter")
    ReportTemplate = apps.get_model("extras", "ReportTemplate")

    EventRule.objects.filter(model=provider_ct).update(model=supplier_ct)
    ExportTemplate.objects.filter(content_type=provider_ct).update(content_type=supplier_ct)
    SavedFilter.objects.filter(content_type=provider_ct).update(content_type=supplier_ct)

    for template in ReportTemplate.objects.all().iterator():
        columns = list(template.included_columns or [])
        renamed_columns = ["supplier" if column == "provider" else column for column in columns]
        group_by = "supplier" if template.group_by_field == "provider" else template.group_by_field
        if renamed_columns != columns or group_by != template.group_by_field:
            template.included_columns = renamed_columns
            template.group_by_field = group_by
            template.save(update_fields=["included_columns", "group_by_field"])

    for saved_filter in SavedFilter.objects.all().iterator():
        parameters = dict(saved_filter.parameters or {})
        if "provider" not in parameters:
            continue
        value = parameters.pop("provider")
        try:
            mapped = provider_to_supplier.get(int(value))
        except (TypeError, ValueError):
            mapped = None
        if mapped is not None:
            parameters["supplier"] = str(mapped)
        saved_filter.parameters = parameters
        saved_filter.save(update_fields=["parameters"])


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0102_commercial_vendor_and_terms"),
        ("assets", "0121_supplier_scoping_and_commercial_fields"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="supplier",
            field=models.ForeignKey(
                help_text="The commercial vendor of this subscription.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="subscriptions",
                to="assets.supplier",
                verbose_name="Supplier",
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="linked_contract",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional procurement contract this subscription relates to.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="linked_subscriptions",
                to="procurement.contract",
                verbose_name="Linked Contract",
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        # The transplant updates assets.supplier rows, so PostgreSQL holds deferred
        # FK trigger events on the supplier table until they are flushed. The
        # AlterField below drops and recreates the supplier FK, which the pending
        # events would block ("cannot ALTER TABLE ... pending trigger events").
        migrations.RunSQL("SET CONSTRAINTS ALL IMMEDIATE", reverse_sql=migrations.RunSQL.noop),
        migrations.AlterField(
            model_name="subscription",
            name="supplier",
            field=models.ForeignKey(
                help_text="The commercial vendor of this subscription.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="subscriptions",
                to="assets.supplier",
                verbose_name="Supplier",
            ),
        ),
        # Reversing the AlterField drops the supplier FK again; flush the reverse
        # transaction's pending events before that happens.
        migrations.RunSQL(migrations.RunSQL.noop, reverse_sql="SET CONSTRAINTS ALL IMMEDIATE"),
        migrations.RemoveField(
            model_name="subscription",
            name="provider",
        ),
        migrations.DeleteModel(
            name="Provider",
        ),
        migrations.AlterModelOptions(
            name="subscription",
            options={
                "ordering": ("-renewal_date", "supplier", "name"),
                "verbose_name": "Subscription",
                "verbose_name_plural": "Subscriptions",
            },
        ),
    ]
