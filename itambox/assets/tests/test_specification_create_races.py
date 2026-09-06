"""PostgreSQL races for Type-create and Category-default apply with observed lock waits."""

from __future__ import annotations

import queue
import threading
import time

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection, connections, transaction

from assets.models.catalog import (
    AssetType,
    AssetTypeFieldset,
    Category,
    CategoryDefaultFieldset,
    Manufacturer,
)
from assets.services.specifications._command_support import resource_revision_for_owner
from assets.services.specifications.commands import (
    apply_category_defaults,
    create_asset_type,
    preview_apply_category_defaults,
    preview_asset_type_create,
)
from assets.services.specifications.contracts import (
    AssetTypeNativeCreateInputDTO,
    CommandRejectedDTO,
    FieldsetSelectionDTO,
    OwnerChangedDTO,
    OwnerCreatedDTO,
    SpecificationPatchDTO,
)
from assets.services.specifications.locking import SPECIFICATION_CATALOGUE_LOCK_KEY, catalogue_transaction_lock
from extras.models import CustomField, CustomFieldset, CustomFieldsetField
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor

User = get_user_model()
pytestmark = [pytest.mark.serial_only, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def create_race_kit():
    assert connection.vendor == "postgresql"
    user = User.objects.create_user(username="create-race-editor")
    user.user_permissions.add(
        Permission.objects.get(content_type=ContentType.objects.get_for_model(AssetType), codename="add_assettype"),
        Permission.objects.get(content_type=ContentType.objects.get_for_model(AssetType), codename="change_assettype"),
    )
    manufacturer = Manufacturer.objects.create(name="Race maker", slug="race-maker")
    category = Category.objects.create(name="Race category", slug="race-category")
    first = CustomFieldset.objects.create(namespace="local", slug="race-first", label="Race first")
    second = CustomFieldset.objects.create(namespace="local", slug="race-second", label="Race second")
    field = CustomField.objects.create(
        name="race_note",
        namespace="local",
        label="Note",
        field_type=CustomField.FIELD_TYPE_TEXT,
        activation=CustomField.ACTIVATION_COMPOSED,
    )
    field.object_types.add(ContentType.objects.get_for_model(AssetType))
    for group in (first, second):
        CustomFieldsetField.objects.create(fieldset=group, custom_field=field, position=1)
    CategoryDefaultFieldset.objects.create(category=category, fieldset=first, position=1)
    actor = ActorContextDTO(actor_id=user.pk, authentication_revision=authentication_revision_for_actor(user))
    return manufacturer, category, first, second, actor


def _native(manufacturer, category, *, model):
    return AssetTypeNativeCreateInputDTO(
        manufacturer_id=manufacturer.pk,
        model=model,
        slug=None,
        part_number="PN",
        ean="4000000000002",
        region="EU",
        configuration="cfg",
        eol_months=None,
        category_id=category.pk,
        suggested_asset_role_id=None,
        depreciation_id=None,
        staged_image_id=None,
        description="description",
        comments="comments",
        tag_ids=(),
        requestable=False,
    )


def _omitted():
    return FieldsetSelectionDTO("omitted", ())


def _create_preview(actor, native):
    return preview_asset_type_create(
        actor=actor,
        native=native,
        fieldsets=_omitted(),
        patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
    )


def _create(actor, native, preview):
    return create_asset_type(
        actor=actor,
        native=native,
        fieldsets=_omitted(),
        patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        preview_token=preview.preview_token,
        expected_definition_revision=preview.expected_definition_revision,
        expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
    )


def _apply_preview(actor, owner):
    return preview_apply_category_defaults(
        actor=actor,
        asset_type_id=owner.pk,
        expected_resource_revision=resource_revision_for_owner(owner),
        patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
    )


def _apply(actor, owner, preview):
    return apply_category_defaults(
        actor=actor,
        asset_type_id=owner.pk,
        preview_token=preview.preview_token,
        expected_resource_revision=preview.expected_resource_revision,
        expected_definition_revision=preview.expected_definition_revision,
        expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
        patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
    )


def _start(target):
    arrived = queue.Queue()
    results, errors = [], []

    def worker():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                arrived.put(cursor.fetchone()[0])
            results.append(target())
        except Exception as error:
            errors.append(error)
        finally:
            connections["default"].close()

    thread = threading.Thread(target=worker)
    thread.start()
    return thread, arrived.get(timeout=10), results, errors


def _assert_waiting(pid):
    """Require PostgreSQL to identify this backend as blocked by our transaction."""
    deadline = time.monotonic() + 10
    last = None
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT wait_event_type, wait_event, pg_backend_pid() = ANY(pg_blocking_pids(pid)) "
                "FROM pg_stat_activity WHERE pid = %s",
                [pid],
            )
            last = cursor.fetchone()
            if last and last[0] == "Lock" and last[2]:
                cursor.execute(
                    "SELECT mode FROM pg_locks WHERE pid = %s AND locktype = 'advisory' "
                    "AND classid = %s AND objid = %s AND NOT granted",
                    [pid, *SPECIFICATION_CATALOGUE_LOCK_KEY],
                )
                assert cursor.fetchone() is not None
                print("OBSERVED_DATABASE_WAIT", pid, last, "catalogue")
                return
        threading.Event().wait(0.01)
    pytest.fail(f"backend {pid} never reached the expected database wait: {last}")


def _finish(started):
    thread, _, results, errors = started
    thread.join(20)
    assert not thread.is_alive(), "worker did not terminate after lock release"
    assert not errors, errors
    assert len(results) == 1
    return results[0]


def test_two_concurrent_creates_consume_category_defaults_serially(create_race_kit):
    manufacturer, category, first, _second, actor = create_race_kit
    native_a = _native(manufacturer, category, model="Race model A")
    native_b = _native(manufacturer, category, model="Race model B")
    preview_a = _create_preview(actor, native_a)
    preview_b = _create_preview(actor, native_b)
    assert preview_a.expected_category_default_snapshot_revision == preview_b.expected_category_default_snapshot_revision

    started = None
    try:
        with transaction.atomic():
            result_a = _create(actor, native_a, preview_a)
            assert isinstance(result_a, OwnerCreatedDTO)
            started = _start(lambda: _create(actor, native_b, preview_b))
            _assert_waiting(started[1])
    finally:
        if started is not None:
            result_b = _finish(started)

    assert isinstance(result_b, OwnerCreatedDTO)
    assert result_a.owner.owner_id != result_b.owner.owner_id
    for result in (result_a, result_b):
        owner = AssetType.all_objects.get(pk=result.owner.owner_id)
        assert list(owner.fieldset_memberships.values_list("fieldset_id", "position")) == [(first.pk, 1)]


@pytest.mark.parametrize("defaults_changed", [False, True])
def test_concurrent_create_and_category_defaults_change_serialize(create_race_kit, defaults_changed):
    manufacturer, category, first, second, actor = create_race_kit
    native = _native(manufacturer, category, model="Race model")
    preview = _create_preview(actor, native)

    started = None
    try:
        with transaction.atomic(), catalogue_transaction_lock(exclusive=True):
            if defaults_changed:
                CategoryDefaultFieldset.objects.create(category=category, fieldset=second, position=2)
            started = _start(lambda: _create(actor, native, preview))
            _assert_waiting(started[1])
    finally:
        if started is not None:
            result = _finish(started)

    if defaults_changed:
        assert isinstance(result, CommandRejectedDTO)
        assert [issue.code for issue in result.issues] == ["STALE_RESOURCE"]
        assert AssetType.all_objects.filter(model="Race model").count() == 0
    else:
        assert isinstance(result, OwnerCreatedDTO)
        owner = AssetType.all_objects.get(pk=result.owner.owner_id)
        assert list(owner.fieldset_memberships.values_list("fieldset_id", "position")) == [(first.pk, 1)]


def test_concurrent_apply_defaults_and_composition_change_one_winner(create_race_kit):
    manufacturer, category, first, second, actor = create_race_kit
    owner = AssetType.objects.create(
        manufacturer=manufacturer,
        model="Race apply type",
        slug="race-apply-type",
        category=category,
    )
    AssetTypeFieldset.objects.create(asset_type=owner, fieldset=first, position=1)
    preview = _apply_preview(actor, owner)

    started = None
    try:
        with transaction.atomic(), catalogue_transaction_lock(exclusive=True):
            AssetTypeFieldset.objects.create(asset_type=owner, fieldset=second, position=2)
            started = _start(lambda: _apply(actor, owner, preview))
            _assert_waiting(started[1])
    finally:
        if started is not None:
            result = _finish(started)

    assert isinstance(result, CommandRejectedDTO)
    assert [issue.code for issue in result.issues] == ["STALE_RESOURCE"]
    owner.refresh_from_db()
    assert list(owner.fieldset_memberships.values_list("fieldset_id", "position")) == [
        (first.pk, 1),
        (second.pk, 2),
    ]