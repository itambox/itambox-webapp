"""PostgreSQL two-connection coordination checks for T09-A."""

from __future__ import annotations

import threading

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection, connections, transaction

from assets.models.catalog import AssetType, AssetTypeFieldset, Manufacturer
from assets.services.specifications._command_support import (
    load_effective_definition,
    resource_revision_for_owner,
)
from assets.services.specifications.commands import update_asset_type_specifications
from assets.services.specifications.contracts import OwnerChangedDTO, SpecificationPatchDTO
from assets.services.specifications.locking import catalogue_transaction_lock
from extras.models import CustomField, CustomFieldset, CustomFieldsetField
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor

User = get_user_model()


@pytest.fixture
def locked_type(db):
    user = User.objects.create_user(username="race-editor")
    user.user_permissions.add(
        Permission.objects.get(
            content_type=ContentType.objects.get_for_model(AssetType),
            codename="change_assettype",
        )
    )
    manufacturer = Manufacturer.objects.create(name="Race maker", slug="race-maker")
    asset_type = AssetType.objects.create(
        manufacturer=manufacturer,
        model="Race type",
        slug="race-type",
    )
    field = CustomField.objects.create(
        name="race_note",
        namespace="local",
        label="Race note",
        field_type=CustomField.FIELD_TYPE_TEXT,
        activation=CustomField.ACTIVATION_COMPOSED,
        management_kind=CustomField.MANAGEMENT_LOCAL,
    )
    field.object_types.add(ContentType.objects.get_for_model(AssetType))
    fieldset = CustomFieldset.objects.create(
        namespace="local",
        slug="race-values",
        label="Race values",
        management_kind=CustomFieldset.MANAGEMENT_LOCAL,
    )
    CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)
    AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=1)
    return asset_type, ActorContextDTO(
        actor_id=user.pk,
        authentication_revision=authentication_revision_for_actor(user),
    )


def _plan(asset_type):
    owner = AssetType.all_objects.get(pk=asset_type.pk)
    definition, _definitions = load_effective_definition(
        owner.pk,
        "asset_type",
        tuple(owner.custom_field_data),
    )
    return resource_revision_for_owner(owner), definition.revision


def _command(asset_type, actor, resource_revision, definition_revision, value):
    return update_asset_type_specifications(
        actor=actor,
        asset_type_id=asset_type.pk,
        expected_resource_revision=resource_revision,
        expected_definition_revision=definition_revision,
        patch=SpecificationPatchDTO(set_values={"race_note": value}, clear_keys=()),
    )


def _thread_call(target, results, errors):
    close_old_connections()
    try:
        results.append(target())
    except Exception as error:  # report thread failures in the test thread
        errors.append(error)
    finally:
        connections["default"].close()


@pytest.mark.serial_only
@pytest.mark.django_db(transaction=True)
def test_postgresql_owner_lock_coordinates_a_second_connection(locked_type):
    if connection.vendor != "postgresql":
        pytest.skip("T09-A race evidence requires PostgreSQL")
    asset_type, actor = locked_type
    resource_revision, definition_revision = _plan(asset_type)
    ready = threading.Event()
    release = threading.Event()
    done = threading.Event()
    results = []
    errors = []

    def holder():
        close_old_connections()
        try:
            with transaction.atomic():
                with catalogue_transaction_lock():
                    locked = AssetType.all_objects.select_for_update().get(pk=asset_type.pk)
                    assert locked.pk == asset_type.pk
                    ready.set()
                    assert release.wait(10), "test holder was not released"
        finally:
            connections["default"].close()

    def writer():
        close_old_connections()
        try:
            results.append(_command(asset_type, actor, resource_revision, definition_revision, "blocked"))
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
            connections["default"].close()

    holder_thread = threading.Thread(target=holder)
    writer_thread = threading.Thread(target=writer)
    holder_thread.start()
    writer_started = False
    try:
        assert ready.wait(10), "test holder did not acquire the PostgreSQL row lock"
        writer_thread.start()
        writer_started = True
        assert not done.wait(0.5), "writer did not wait for the locked owner row"
    finally:
        release.set()
        holder_thread.join(15)
        if writer_started:
            writer_thread.join(15)

    assert not holder_thread.is_alive(), "owner lock holder did not terminate"
    assert not writer_thread.is_alive(), "owner lock writer did not terminate"
    assert not errors
    assert len(results) == 1
    assert isinstance(results[0], OwnerChangedDTO)
    assert AssetType.all_objects.get(pk=asset_type.pk).custom_field_data == {"race_note": "blocked"}


@pytest.mark.serial_only
@pytest.mark.django_db(transaction=True)
def test_postgresql_shared_and_exclusive_catalogue_locks_coordinate(locked_type):
    if connection.vendor != "postgresql":
        pytest.skip("T09-A race evidence requires PostgreSQL")
    asset_type, _actor = locked_type
    exclusive_ready = threading.Event()
    shared_acquired = threading.Event()
    release = threading.Event()
    errors = []

    def exclusive_holder():
        close_old_connections()
        try:
            with transaction.atomic():
                with catalogue_transaction_lock(exclusive=True):
                    exclusive_ready.set()
                    assert release.wait(10), "exclusive holder was not released"
        except Exception as error:
            errors.append(error)
        finally:
            connections["default"].close()

    def shared_waiter():
        close_old_connections()
        try:
            with transaction.atomic():
                with catalogue_transaction_lock():
                    shared_acquired.set()
        except Exception as error:
            errors.append(error)
        finally:
            connections["default"].close()

    holder_thread = threading.Thread(target=exclusive_holder)
    waiter_thread = threading.Thread(target=shared_waiter)
    holder_thread.start()
    waiter_started = False
    try:
        assert exclusive_ready.wait(10), "exclusive holder did not acquire the catalogue lock"
        waiter_thread.start()
        waiter_started = True
        assert not shared_acquired.wait(0.5), "shared waiter bypassed the exclusive catalogue lock"
    finally:
        release.set()
        holder_thread.join(15)
        if waiter_started:
            waiter_thread.join(15)

    assert not holder_thread.is_alive(), "exclusive lock thread did not terminate"
    assert not waiter_thread.is_alive(), "shared lock thread did not terminate"
    assert not errors
    assert shared_acquired.is_set()
    assert asset_type.pk is not None


@pytest.mark.serial_only
@pytest.mark.django_db(transaction=True)
def test_postgresql_same_owner_race_has_one_winner_and_no_lost_update(locked_type):
    if connection.vendor != "postgresql":
        pytest.skip("T09-A race evidence requires PostgreSQL")
    asset_type, actor = locked_type
    resource_revision, definition_revision = _plan(asset_type)
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def call(value):
        barrier.wait(10)
        return _command(asset_type, actor, resource_revision, definition_revision, value)

    threads = [
        threading.Thread(
            target=_thread_call,
            args=(lambda value=value: call(value), results, errors),
        )
        for value in ("first", "second")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert all(not thread.is_alive() for thread in threads), "race worker did not terminate"
    assert not errors
    assert len(results) == 2
    changed = [result for result in results if isinstance(result, OwnerChangedDTO)]
    rejected = [result for result in results if getattr(result, "outcome", None) == "rejected"]
    assert len(changed) == 1
    assert len(rejected) == 1
    assert [issue.code for issue in rejected[0].issues] == ["STALE_RESOURCE"]
    assert AssetType.all_objects.get(pk=asset_type.pk).custom_field_data in (
        {"race_note": "first"},
        {"race_note": "second"},
    )
