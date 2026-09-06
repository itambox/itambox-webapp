"""PostgreSQL races with backend arrival and observed database lock waits."""

from __future__ import annotations

import queue
import threading
import time

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection, connections, transaction

from assets.models.catalog import AssetType, AssetTypeFieldset, Manufacturer
from assets.services.specifications._command_support import load_effective_definition, resource_revision_for_owner
from assets.services.specifications.commands import set_asset_type_composition, update_asset_type_specifications
from assets.services.specifications.contracts import (
    CommandRejectedDTO,
    ExplicitFieldsetSelectionDTO,
    OwnerChangedDTO,
    SpecificationPatchDTO,
)
from assets.services.specifications.locking import SPECIFICATION_CATALOGUE_LOCK_KEY, catalogue_transaction_lock
from extras.models import CustomField, CustomFieldset, CustomFieldsetField, SpecificationLibrary
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor

User = get_user_model()
pytestmark = [pytest.mark.serial_only, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def composition_race_type():
    assert connection.vendor == "postgresql"
    user = User.objects.create_user(username="composition-race-editor")
    user.user_permissions.add(
        Permission.objects.get(content_type=ContentType.objects.get_for_model(AssetType), codename="change_assettype")
    )
    manufacturer = Manufacturer.objects.create(name="Composition race maker", slug="composition-race-maker")
    owner = AssetType.objects.create(
        manufacturer=manufacturer, model="Composition race type", slug="composition-race-type"
    )
    first = CustomFieldset.objects.create(namespace="local", slug="race-first", label="Race first")
    second = CustomFieldset.objects.create(namespace="local", slug="race-second", label="Race second")
    field = CustomField.objects.create(
        name="composition_race_note",
        namespace="local",
        label="Note",
        field_type=CustomField.FIELD_TYPE_TEXT,
        activation=CustomField.ACTIVATION_COMPOSED,
    )
    field.object_types.add(ContentType.objects.get_for_model(AssetType))
    for group in (first, second):
        CustomFieldsetField.objects.create(fieldset=group, custom_field=field, position=1)
    AssetTypeFieldset.objects.create(asset_type=owner, fieldset=first, position=1)
    actor = ActorContextDTO(actor_id=user.pk, authentication_revision=authentication_revision_for_actor(user))
    return owner, first, second, actor


def _type_plan(owner):
    owner.refresh_from_db()
    definition, _ = load_effective_definition(owner.pk, "asset_type", tuple(owner.custom_field_data))
    return resource_revision_for_owner(owner), definition.revision


def _composition(owner, second, actor, plan):
    return set_asset_type_composition(
        actor=actor,
        asset_type_id=owner.pk,
        fieldsets=ExplicitFieldsetSelectionDTO((f"{second.namespace}/{second.slug}",)),
        expected_resource_revision=plan[0],
        expected_definition_revision=plan[1],
        patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
    )


def _value(owner, actor, plan):
    return update_asset_type_specifications(
        actor=actor,
        asset_type_id=owner.pk,
        expected_resource_revision=plan[0],
        expected_definition_revision=plan[1],
        patch=SpecificationPatchDTO(set_values={"composition_race_note": "writer value"}, clear_keys=()),
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


def _assert_waiting(pid, *, advisory=True):
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
                if advisory:
                    cursor.execute(
                        "SELECT mode FROM pg_locks WHERE pid = %s AND locktype = 'advisory' "
                        "AND classid = %s AND objid = %s AND NOT granted",
                        [pid, *SPECIFICATION_CATALOGUE_LOCK_KEY],
                    )
                    assert cursor.fetchone() is not None
                print("OBSERVED_DATABASE_WAIT", pid, last, "catalogue" if advisory else "library")
                return
        threading.Event().wait(0.01)
    pytest.fail(f"backend {pid} never reached the expected database wait: {last}")


def _finish(started):
    thread, _, results, errors = started
    thread.join(15)
    assert not thread.is_alive(), "worker did not terminate after lock release"
    assert not errors, errors
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize("first_writer", ["value", "composition"])
def test_actual_composition_and_value_commands_serialize_and_reject_stale_plan(composition_race_type, first_writer):
    owner, first, second, actor = composition_race_type
    plan = _type_plan(owner)
    started = None
    try:
        with transaction.atomic():
            if first_writer == "value":
                result = _value(owner, actor, plan)

                def target():
                    return _composition(owner, second, actor, plan)
            else:
                result = _composition(owner, second, actor, plan)

                def target():
                    return _value(owner, actor, plan)

            assert isinstance(result, OwnerChangedDTO)
            started = _start(target)
            _assert_waiting(started[1])
    finally:
        if started is not None:
            rejected = _finish(started)
    assert isinstance(rejected, CommandRejectedDTO)
    assert rejected.issues[0].code == "STALE_RESOURCE"
    owner.refresh_from_db()
    expected_group = first if first_writer == "value" else second
    assert list(owner.fieldset_memberships.values_list("fieldset_id", "position")) == [(expected_group.pk, 1)]
    assert owner.custom_field_data == ({"composition_race_note": "writer value"} if first_writer == "value" else {})


@pytest.mark.parametrize(
    "change,code",
    [
        ("resource", "STALE_RESOURCE"),
        ("definition", "STALE_DEFINITION"),
        ("permission", "OBJECT_UNAVAILABLE"),
        ("inactive", "OBJECT_UNAVAILABLE"),
    ],
)
def test_composition_reauthorizes_and_reloads_after_observed_wait(composition_race_type, change, code):
    owner, first, second, actor = composition_race_type
    plan = _type_plan(owner)
    started = None
    try:
        with transaction.atomic(), catalogue_transaction_lock(exclusive=True):
            started = _start(lambda: _composition(owner, second, actor, plan))
            _assert_waiting(started[1])
            if change == "resource":
                AssetType._base_manager.filter(pk=owner.pk).update(model="changed while command waits")
            elif change == "definition":
                CustomField.objects.filter(name="composition_race_note").update(label="changed while command waits")
            elif change == "permission":
                User.objects.get(pk=actor.actor_id).user_permissions.clear()
            else:
                User.objects.filter(pk=actor.actor_id).update(is_active=False)
    finally:
        if started is not None:
            result = _finish(started)
    assert isinstance(result, CommandRejectedDTO)
    assert [issue.code for issue in result.issues] == [code]
    if code == "OBJECT_UNAVAILABLE":
        assert result.safe_owner is None
    assert list(owner.fieldset_memberships.values_list("fieldset_id", "position")) == [(first.pk, 1)]
    owner.refresh_from_db()
    assert owner.custom_field_data == {}


@pytest.mark.parametrize("locked_side", ["current", "proposed"])
def test_empty_current_and_proposed_library_fieldsets_contend_before_owner_lock(composition_race_type, locked_side):
    owner, _, _, actor = composition_race_type
    groups = []
    libraries = []
    for suffix in ("current", "proposed"):
        library = SpecificationLibrary.objects.create(namespace=f"race-{suffix}", label=suffix)
        libraries.append(library)
        groups.append(
            CustomFieldset.objects.create(
                namespace=library.namespace,
                slug="empty",
                label=suffix,
                management_kind=CustomFieldset.MANAGEMENT_LIBRARY,
                library=library,
            )
        )
    owner.fieldset_memberships.all().delete()
    AssetTypeFieldset.objects.create(asset_type=owner, fieldset=groups[0], position=1)
    plan = _type_plan(owner)
    started = None
    try:
        with transaction.atomic():
            SpecificationLibrary.objects.select_for_update().get(pk=libraries[locked_side == "proposed"].pk)
            started = _start(lambda: _composition(owner, groups[1], actor, plan))
            _assert_waiting(started[1], advisory=False)
            # The waiting command must not have taken the owner lock first.
            AssetType.all_objects.select_for_update(nowait=True).get(pk=owner.pk)
    finally:
        if started is not None:
            result = _finish(started)
    assert isinstance(result, OwnerChangedDTO)
    assert list(owner.fieldset_memberships.values_list("fieldset_id", "position")) == [(groups[1].pk, 1)]
