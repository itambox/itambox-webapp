"""PostgreSQL contention proof for definition and value command lock ordering."""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection, connections

from assets.models.catalog import AssetType, Manufacturer
from assets.services.specifications._command_support import load_effective_definition, resource_revision_for_owner
from assets.services.specifications.commands import update_asset_type_specifications
from assets.services.specifications.contracts import OwnerChangedDTO, SpecificationPatchDTO
from assets.services.specifications.locking import SPECIFICATION_CATALOGUE_LOCK_KEY, catalogue_transaction_lock
from extras.models import CustomField, CustomFieldset, CustomFieldsetField
from extras.services._definition_command_support import resource_revision_for_definition
from extras.services.definition_command_contracts import CustomFieldUpdateInputDTO
from extras.services.definition_commands import update_custom_field
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor

User = get_user_model()
pytestmark = [pytest.mark.serial_only, pytest.mark.django_db(transaction=True)]


def _start_worker(target):
    arrived = queue.Queue()
    results = []
    errors = []

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
    pid = arrived.get(timeout=10)
    return thread, pid, results, errors


def _finish_worker(started):
    thread, _pid, results, errors = started
    thread.join(20)
    assert not thread.is_alive(), "database worker did not terminate after lock release"
    assert not errors, errors
    assert len(results) == 1
    return results[0]


def _assert_shared_lock_waiting(pid, blocker_pid):
    """Observe PostgreSQL's real blocker and pending shared advisory lock."""
    deadline = threading.Event()
    last_activity = None
    for _ in range(1000):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                """
                SELECT wait_event_type, wait_event, pg_blocking_pids(pid)
                FROM pg_stat_activity
                WHERE pid = %s
                """,
                [pid],
            )
            last_activity = cursor.fetchone()
            if last_activity and last_activity[0] == "Lock" and blocker_pid in last_activity[2]:
                cursor.execute(
                    """
                    SELECT mode
                    FROM pg_locks
                    WHERE pid = %s
                      AND locktype = 'advisory'
                      AND classid = %s
                      AND objid = %s
                      AND NOT granted
                    """,
                    [pid, *SPECIFICATION_CATALOGUE_LOCK_KEY],
                )
                pending_lock = cursor.fetchone()
                if pending_lock is not None:
                    assert pending_lock[0] == "ShareLock"
                    print(
                        "OBSERVED_DATABASE_WAIT",
                        {"waiter": pid, "blocker": blocker_pid, "activity": last_activity, "mode": pending_lock[0]},
                    )
                    return
        deadline.wait(0.01)
    pytest.fail(f"shared value command never reached the expected database wait: {last_activity}")


@pytest.fixture
def definition_race_kit():
    assert connection.vendor == "postgresql"
    user = User.objects.create_user(username="definition-race-editor")
    user.user_permissions.add(
        Permission.objects.get(
            content_type=ContentType.objects.get_for_model(CustomField),
            codename="change_customfield",
        ),
        Permission.objects.get(
            content_type=ContentType.objects.get_for_model(AssetType),
            codename="change_assettype",
        ),
    )
    manufacturer = Manufacturer.objects.create(name="Definition race maker", slug="definition-race-maker")
    asset_type = AssetType.objects.create(
        manufacturer=manufacturer,
        model="Definition race type",
        slug="definition-race-type",
    )
    field = CustomField.objects.create(
        name="definition_race_value",
        namespace="local",
        label="Definition race value",
        field_type=CustomField.FIELD_TYPE_TEXT,
        activation=CustomField.ACTIVATION_COMPOSED,
        management_kind=CustomField.MANAGEMENT_LOCAL,
    )
    field.object_types.add(ContentType.objects.get_for_model(AssetType))
    fieldset = CustomFieldset.objects.create(
        namespace="local",
        slug="definition-race-values",
        label="Definition race values",
        management_kind=CustomFieldset.MANAGEMENT_LOCAL,
    )
    CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)
    asset_type.fieldset_memberships.create(fieldset=fieldset, position=1)
    actor = ActorContextDTO(
        actor_id=user.pk,
        authentication_revision=authentication_revision_for_actor(user),
    )
    return asset_type, field, actor


def test_exclusive_definition_and_shared_value_commands_observe_catalogue_contention(definition_race_kit):
    asset_type, field, actor = definition_race_kit
    owner = AssetType.all_objects.get(pk=asset_type.pk)
    definition, _definitions = load_effective_definition(
        owner.pk,
        "asset_type",
        tuple(owner.custom_field_data),
    )
    owner_revision = resource_revision_for_owner(owner)
    definition_revision = definition.revision
    field_revision = resource_revision_for_definition(field)
    exclusive_ready = threading.Event()
    release_exclusive = threading.Event()
    real_catalogue_lock = catalogue_transaction_lock

    @contextmanager
    def held_exclusive_lock(*, exclusive=False, using="default"):
        with real_catalogue_lock(exclusive=exclusive, using=using):
            if exclusive:
                exclusive_ready.set()
                assert release_exclusive.wait(20), "exclusive definition command was not released"
            yield

    definition_started = None
    value_started = None
    try:
        with patch("extras.services.definition_commands.catalogue_transaction_lock", held_exclusive_lock):
            definition_started = _start_worker(
                lambda: update_custom_field(
                    actor=actor,
                    field_id=field.pk,
                    expected_resource_revision=field_revision,
                    changes=CustomFieldUpdateInputDTO(),
                )
            )
            assert exclusive_ready.wait(10), "definition command did not acquire the exclusive catalogue lock"
            value_started = _start_worker(
                lambda: update_asset_type_specifications(
                    actor=actor,
                    asset_type_id=owner.pk,
                    expected_resource_revision=owner_revision,
                    expected_definition_revision=definition_revision,
                    patch=SpecificationPatchDTO(
                        set_values={"definition_race_value": "serialized"},
                        clear_keys=(),
                    ),
                )
            )
            assert definition_started[1] != value_started[1]
            _assert_shared_lock_waiting(value_started[1], definition_started[1])
    finally:
        release_exclusive.set()
        if definition_started is not None:
            definition_result = _finish_worker(definition_started)
        if value_started is not None:
            value_result = _finish_worker(value_started)

    assert getattr(definition_result, "outcome", None) == "no_op"
    assert isinstance(value_result, OwnerChangedDTO)
    owner.refresh_from_db()
    assert owner.custom_field_data == {"definition_race_value": "serialized"}
