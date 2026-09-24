"""Move subscription providers into the shared supplier catalogue.

When names match within a scope, live suppliers are preferred over tombstones;
a live provider creates a fresh supplier when only a tombstone matches. An
available provider slug is retained; collisions use slugify(name) and then
numeric suffixes, checked against live rows in the applicable unique scope.
Provider admin_notes become Supplier notes on newly created rows. Matched
suppliers receive portal_url and account_id only when their fields are empty.
Provider deleted_at values are preserved on newly created suppliers. This data
cutover is one-way: rollback requires restoring from backup.
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
        tag_ids = provider_through.objects.filter(**{provider_owner_attname: provider_id}).order_by(
            provider_tag_attname
        ).values_list(provider_tag_attname, flat=True)
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
                "ContactAssignment references a missing subscriptions.Provider row: "
                f"{assignment.object_id}"
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
