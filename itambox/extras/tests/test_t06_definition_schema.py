import threading
import time

import pytest
from django.core.exceptions import FieldDoesNotExist
from django.db import DatabaseError, IntegrityError, close_old_connections, connections, models, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import override_settings

from assets.models.catalog import AssetType, AssetTypeFieldset, CategoryDefaultFieldset
from extras.models import (
    CustomField,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomFieldset,
    CustomFieldsetField,
)


_RACE_TIMEOUT_SECONDS = 5


@override_settings(ITAMBOX_ENV="dev")
class T06DefinitionSchemaTests(TestCase):
    def _field(self, name, *, activation=CustomField.ACTIVATION_GLOBAL, lifecycle=CustomField.LIFECYCLE_ACTIVE):
        field = CustomField.objects.create(
            name=name,
            namespace="local",
            label=name,
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=activation,
            lifecycle=lifecycle,
        )
        field.object_types.add(self.asset_type_content_type)
        return field

    @classmethod
    def setUpTestData(cls):
        from django.contrib.contenttypes.models import ContentType

        cls.asset_type_content_type = ContentType.objects.get_for_model(AssetType)

    def test_reusable_definitions_have_permanent_lifecycle_without_soft_delete_state(self):
        for model in (CustomField, CustomFieldset, CustomFieldChoiceSet, CustomFieldChoice):
            with self.subTest(model=model.__name__):
                with self.assertRaises(FieldDoesNotExist):
                    model._meta.get_field("deleted_at")
                self.assertFalse(hasattr(model, "restore"))
                self.assertNotIn("SoftDeleteManager", type(model._default_manager).__name__)

    def test_custom_field_has_required_explicit_activation_and_no_scope_property(self):
        activation = CustomField._meta.get_field("activation")

        self.assertFalse(activation.null)
        self.assertIs(activation.default, models.NOT_PROVIDED)
        with self.assertRaises(FieldDoesNotExist):
            CustomField._meta.get_field("scope")

    def test_ordering_position_constraints_are_deferred(self):
        for model, constraint_name in (
            (CustomFieldsetField, "unique_customfieldset_position"),
            (CustomFieldChoice, "unique_customfieldchoice_position"),
            (AssetTypeFieldset, "unique_assettype_fieldset_position"),
            (CategoryDefaultFieldset, "unique_category_default_position"),
        ):
            constraint = next(item for item in model._meta.constraints if item.name == constraint_name)
            with self.subTest(model=model.__name__):
                self.assertEqual(constraint.deferrable, models.Deferrable.DEFERRED)

    def test_global_field_cannot_join_a_fieldset(self):
        field = self._field("global_only")
        fieldset = CustomFieldset.objects.create(namespace="local", slug="global-only", label="Global only")

        with self.assertRaises((IntegrityError, DatabaseError)):
            CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)

    def test_removing_last_membership_does_not_promote_composed_field(self):
        field = self._field("composed_field", activation=CustomField.ACTIVATION_COMPOSED)
        fieldset = CustomFieldset.objects.create(namespace="local", slug="composed", label="Composed")
        membership = CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)

        membership.delete()
        field.refresh_from_db()

        self.assertEqual(field.activation, CustomField.ACTIVATION_COMPOSED)

    def test_global_activation_switch_with_membership_is_database_guarded(self):
        field = self._field("activation_guard", activation=CustomField.ACTIVATION_COMPOSED)
        fieldset = CustomFieldset.objects.create(namespace="local", slug="activation-guard", label="Guard")
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)

        with self.assertRaises((IntegrityError, DatabaseError)):
            with transaction.atomic():
                CustomField.objects.filter(pk=field.pk).update(activation=CustomField.ACTIVATION_GLOBAL)

        field.refresh_from_db()
        self.assertEqual(field.activation, CustomField.ACTIVATION_COMPOSED)

    def test_queryset_delete_cannot_bypass_permanent_definition_guard(self):
        field = self._field("queryset_delete_guard")

        with self.assertRaises((IntegrityError, DatabaseError)):
            with transaction.atomic():
                CustomField.objects.filter(pk=field.pk).delete()

        self.assertTrue(CustomField.objects.filter(pk=field.pk).exists())

    def test_reusable_definition_identity_cannot_be_deleted(self):
        field = self._field("permanent_field")

        with self.assertRaises((IntegrityError, DatabaseError)):
            field.delete()

    def test_deprecated_choice_history_is_still_a_real_row(self):
        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="history",
            label="History",
            lifecycle=CustomFieldChoiceSet.LIFECYCLE_DEPRECATED,
        )
        choice = CustomFieldChoice.objects.create(
            choice_set=choice_set,
            key="retired",
            label="Retired",
            position=1,
            lifecycle=CustomFieldChoice.LIFECYCLE_DEPRECATED,
        )

        self.assertTrue(CustomFieldChoice.objects.filter(pk=choice.pk).exists())
        with self.assertRaises((IntegrityError, DatabaseError)):
            choice.delete()


@pytest.mark.serial_only
@override_settings(ITAMBOX_ENV="dev")
class T06GlobalMembershipConcurrencyTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.contenttypes.models import ContentType

        self.field = CustomField.objects.create(
            name="concurrent_guard",
            namespace="local",
            label="Concurrent guard",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            lifecycle=CustomField.LIFECYCLE_ACTIVE,
        )
        self.field.object_types.add(ContentType.objects.get_for_model(AssetType))
        self.fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="concurrent-guard",
            label="Concurrent guard",
        )

    @staticmethod
    def _backend_pid(db):
        with db.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            return cursor.fetchone()[0]

    def _wait_for_row_lock(self, waiting_pid, blocking_pid, statement_finished, worker_finished):
        deadline = time.monotonic() + _RACE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if statement_finished.is_set() or worker_finished.is_set():
                return False
            with connections["default"].cursor() as cursor:
                cursor.execute(
                    """
                    SELECT wait_event_type, pg_blocking_pids(pid)
                    FROM pg_stat_activity
                    WHERE pid = %s
                    """,
                    [waiting_pid],
                )
                state = cursor.fetchone()
            if state and state[0] == "Lock" and blocking_pid in state[1]:
                return True
            time.sleep(0.02)
        return False

    def test_membership_insert_serializes_after_activation_update(self):
        activation_ready = threading.Event()
        membership_started = threading.Event()
        release_activation = threading.Event()
        release_membership = threading.Event()
        membership_statement_finished = threading.Event()
        activation_finished = threading.Event()
        membership_finished = threading.Event()
        pids = {}
        errors = {"activation": [], "membership": []}

        def activation_worker():
            close_old_connections()
            db = connections["default"]
            try:
                with transaction.atomic(using="default"):
                    pids["activation"] = self._backend_pid(db)
                    with db.cursor() as cursor:
                        cursor.execute(
                            "UPDATE extras_customfield SET activation = %s WHERE id = %s",
                            [CustomField.ACTIVATION_GLOBAL, self.field.pk],
                        )
                    activation_ready.set()
                    if not release_activation.wait(_RACE_TIMEOUT_SECONDS):
                        raise AssertionError("activation worker was not released")
            except BaseException as exc:
                errors["activation"].append(exc)
            finally:
                activation_finished.set()
                close_old_connections()

        def membership_worker():
            close_old_connections()
            db = connections["default"]
            try:
                with transaction.atomic(using="default"):
                    pids["membership"] = self._backend_pid(db)
                    membership_started.set()
                    with db.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO extras_customfieldsetfield
                                (fieldset_id, custom_field_id, position, created_at, updated_at)
                            VALUES (%s, %s, %s, NOW(), NOW())
                            """,
                            [self.fieldset.pk, self.field.pk, 1],
                        )
                    membership_statement_finished.set()
                    if not release_membership.wait(_RACE_TIMEOUT_SECONDS):
                        raise AssertionError("membership worker was not released")
            except BaseException as exc:
                errors["membership"].append(exc)
            finally:
                membership_finished.set()
                close_old_connections()

        activation_thread = threading.Thread(target=activation_worker, name="t06-activation-first")
        membership_thread = threading.Thread(target=membership_worker, name="t06-membership-after-activation")
        activation_thread.start()
        membership_thread_started = False
        try:
            self.assertTrue(activation_ready.wait(_RACE_TIMEOUT_SECONDS))
            membership_thread.start()
            membership_thread_started = True
            self.assertTrue(membership_started.wait(_RACE_TIMEOUT_SECONDS))
            self.assertTrue(
                self._wait_for_row_lock(
                    pids["membership"],
                    pids["activation"],
                    membership_statement_finished,
                    membership_finished,
                ),
                "membership INSERT must wait on the CustomField row lock",
            )
            release_activation.set()
            activation_thread.join(_RACE_TIMEOUT_SECONDS)
            release_membership.set()
            membership_thread.join(_RACE_TIMEOUT_SECONDS)
        finally:
            release_activation.set()
            release_membership.set()
            activation_thread.join(_RACE_TIMEOUT_SECONDS)
            if membership_thread_started:
                membership_thread.join(_RACE_TIMEOUT_SECONDS)
        self.assertFalse(activation_thread.is_alive())
        self.assertFalse(membership_thread.is_alive())
        self.assertFalse(errors["activation"])
        self.assertEqual(len(errors["membership"]), 1)
        self.assertIsInstance(errors["membership"][0], DatabaseError)
        self.assertIn("Global Custom Fields cannot join Fieldsets", str(errors["membership"][0]))
        self.field.refresh_from_db()
        self.assertEqual(self.field.activation, CustomField.ACTIVATION_GLOBAL)
        self.assertFalse(CustomFieldsetField.objects.filter(custom_field_id=self.field.pk).exists())

    def test_activation_update_serializes_after_membership_insert(self):
        membership_ready = threading.Event()
        activation_started = threading.Event()
        release_membership = threading.Event()
        release_activation = threading.Event()
        activation_statement_finished = threading.Event()
        membership_finished = threading.Event()
        activation_finished = threading.Event()
        pids = {}
        errors = {"activation": [], "membership": []}

        def membership_worker():
            close_old_connections()
            db = connections["default"]
            try:
                with transaction.atomic(using="default"):
                    pids["membership"] = self._backend_pid(db)
                    with db.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO extras_customfieldsetfield
                                (fieldset_id, custom_field_id, position, created_at, updated_at)
                            VALUES (%s, %s, %s, NOW(), NOW())
                            """,
                            [self.fieldset.pk, self.field.pk, 1],
                        )
                    membership_ready.set()
                    if not release_membership.wait(_RACE_TIMEOUT_SECONDS):
                        raise AssertionError("membership worker was not released")
            except BaseException as exc:
                errors["membership"].append(exc)
            finally:
                membership_finished.set()
                close_old_connections()

        def activation_worker():
            close_old_connections()
            db = connections["default"]
            try:
                with transaction.atomic(using="default"):
                    pids["activation"] = self._backend_pid(db)
                    activation_started.set()
                    with db.cursor() as cursor:
                        cursor.execute(
                            "UPDATE extras_customfield SET activation = %s WHERE id = %s",
                            [CustomField.ACTIVATION_GLOBAL, self.field.pk],
                        )
                    activation_statement_finished.set()
                    if not release_activation.wait(_RACE_TIMEOUT_SECONDS):
                        raise AssertionError("activation worker was not released")
            except BaseException as exc:
                errors["activation"].append(exc)
            finally:
                activation_finished.set()
                close_old_connections()

        membership_thread = threading.Thread(target=membership_worker, name="t06-membership-first")
        activation_thread = threading.Thread(target=activation_worker, name="t06-activation-after-membership")
        membership_thread.start()
        activation_thread_started = False
        try:
            self.assertTrue(membership_ready.wait(_RACE_TIMEOUT_SECONDS))
            activation_thread.start()
            activation_thread_started = True
            self.assertTrue(activation_started.wait(_RACE_TIMEOUT_SECONDS))
            self.assertTrue(
                self._wait_for_row_lock(
                    pids["activation"],
                    pids["membership"],
                    activation_statement_finished,
                    activation_finished,
                ),
                "activation UPDATE must wait on the CustomField row lock",
            )
            release_membership.set()
            membership_thread.join(_RACE_TIMEOUT_SECONDS)
            release_activation.set()
            activation_thread.join(_RACE_TIMEOUT_SECONDS)
        finally:
            release_membership.set()
            release_activation.set()
            membership_thread.join(_RACE_TIMEOUT_SECONDS)
            if activation_thread_started:
                activation_thread.join(_RACE_TIMEOUT_SECONDS)
        self.assertFalse(membership_thread.is_alive())
        self.assertFalse(activation_thread.is_alive())
        self.assertFalse(errors["membership"])
        self.assertEqual(len(errors["activation"]), 1)
        self.assertIsInstance(errors["activation"][0], DatabaseError)
        self.assertIn("A Custom Field with memberships cannot become global", str(errors["activation"][0]))
        self.field.refresh_from_db()
        self.assertEqual(self.field.activation, CustomField.ACTIVATION_COMPOSED)
        self.assertTrue(CustomFieldsetField.objects.filter(custom_field_id=self.field.pk).exists())
