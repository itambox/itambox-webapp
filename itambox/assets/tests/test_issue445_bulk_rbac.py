"""RED execution-time RBAC tests for issue #445 bulk asset workers."""

import importlib
import logging
from contextlib import ExitStack
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from assets.models import (
    Asset,
    AssetAssignment,
    AssetDisposal,
    AssetRole,
    AssetType,
    Manufacturer,
    StatusLabel,
)
from core.models import Job, Notification, ObjectChange
from core.tasks.utils import TaskResult, TaskStatus
from core.tests.mixins import grant
from organization.models import Location, Role, Site, Tenant

User = get_user_model()


FAMILIES = {
    "checkin": {
        "module": "assets.tasks.checkin",
        "old_module": "assets.tasks.checkin",
        "callable": "bulk_checkin_task",
        "permission": "assets.change_asset",
        "code": "checkin.permission_revoked",
        "service": "checkin_asset",
    },
    "checkout": {
        "module": "assets.tasks.checkout",
        "old_module": "assets.tasks.checkout",
        "callable": "bulk_checkout_task",
        "permission": "assets.change_asset",
        "code": "checkout.permission_revoked",
        "service": "checkout_asset",
    },
    "disposal": {
        "module": "assets.tasks.disposal",
        "old_module": "assets.tasks.disposal",
        "callable": "bulk_dispose_task",
        "permission": "assets.add_assetdisposal",
        "code": "disposal.permission_revoked",
        "service": "dispose_asset",
    },
}

# ``assertLogs`` formats each captured record before returning it, which adds
# the standard derived ``message`` attribute after ``makeLogRecord`` runs.
STANDARD_LOG_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__) | {"message"}


def _task_module(config):
    try:
        return importlib.import_module(config["module"])
    except ImportError:
        return importlib.import_module(config["old_module"])


def _snapshot(instance):
    return {field.attname: getattr(instance, field.attname) for field in instance._meta.concrete_fields}


class Issue445BulkWorkerRBACBase:
    family = None

    def setUp(self):
        self.config = FAMILIES[self.family]
        self.tenant = Tenant.objects.create(name=f"Issue445 {self.family}", slug=f"issue445-{self.family}")
        self.user = User.objects.create_user(username=f"issue445-{self.family}-user", password="pw")
        self.role = Role.objects.create(
            tenant=self.tenant,
            name=f"Issue445 {self.family} worker",
            permissions=["assets.change_asset", "assets.add_assetdisposal"],
        )
        grant(self.user, self.tenant, self.role)
        manufacturer = Manufacturer.objects.create(
            name=f"Issue445 {self.family} manufacturer", slug=f"issue445-{self.family}-manufacturer"
        )
        asset_role = AssetRole.objects.create(name=f"Issue445 {self.family} role", slug=f"issue445-{self.family}-role")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model=f"Issue445 {self.family} model",
            slug=f"issue445-{self.family}-model",
        )
        status = StatusLabel.objects.create(
            name=f"Issue445 {self.family} available",
            slug=f"issue445-{self.family}-available",
            type=StatusLabel.TYPE_DEPLOYABLE,
        )
        site = Site.objects.create(
            name=f"Issue445 {self.family} site", slug=f"issue445-{self.family}-site", tenant=self.tenant
        )
        self.location = Location.objects.create(
            name=f"Issue445 {self.family} location",
            slug=f"issue445-{self.family}-location",
            tenant=self.tenant,
            site=site,
        )
        self.asset = Asset.objects.create(
            name=f"Issue445 {self.family} asset",
            asset_tag=f"ISSUE445-{self.family.upper()}",
            asset_type=asset_type,
            asset_role=asset_role,
            status=status,
            tenant=self.tenant,
        )
        self.job = Job.objects.create(name=f"Issue445 {self.family} job", tenant=self.tenant)

    def _invoke(self, task):
        common = {
            "job_id": self.job.pk,
            "asset_pks": [self.asset.pk],
            "user_id": self.user.pk,
            "tenant_id": self.tenant.pk,
        }
        if self.family == "checkout":
            return task(target_type_str="location", target_pk=self.location.pk, notes="", **common)
        if self.family == "disposal":
            return task(disposal_kwargs={}, **common)
        return task(**common)

    def _assert_denied(self, *, revoke_after_warm):
        required = self.config["permission"]
        if revoke_after_warm:
            self.assertTrue(self.user.has_perm(required, obj=self.tenant), "enqueue-time fixture must initially allow")
            self.role.permissions = [permission for permission in self.role.permissions if permission != required]
            self.role.save(update_fields=["permissions"])
        self.assertFalse(
            self.user.has_perm(required, obj=self.tenant), "live permission revocation fixture did not take effect"
        )

        module = _task_module(self.config)
        task = getattr(module, self.config["callable"])
        asset_before = _snapshot(self.asset)
        assignment_count = AssetAssignment.all_objects.filter(asset=self.asset).count()
        disposal_count = AssetDisposal.all_objects.filter(asset=self.asset).count()
        notification_count = Notification.objects.filter(user=self.user).count()
        asset_ct = ContentType.objects.get_for_model(Asset)
        change_count = ObjectChange.objects.filter(
            changed_object_type=asset_ct,
            changed_object_id=self.asset.pk,
        ).count()

        import assets.services as services

        service_owner = module if hasattr(module, self.config["service"]) else services
        with ExitStack() as stack:
            service = stack.enter_context(
                mock.patch.object(
                    service_owner,
                    self.config["service"],
                    side_effect=AssertionError("worker authorization ran after domain service resolution"),
                )
            )
            logs = stack.enter_context(self.assertLogs(module.__name__, level=logging.WARNING))
            result = self._invoke(task)

        self.assertIsInstance(result, TaskResult, "missing issue445 typed bulk denial result contract")
        self.assertEqual(result.status, TaskStatus.TERMINAL, "missing issue445 terminal bulk denial contract")
        self.assertEqual(result.code, self.config["code"], f"missing issue445 {self.family} live permission contract")
        service.assert_not_called()
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.STATUS_FAILED, "missing issue445 failed Job denial contract")
        self.asset.refresh_from_db()
        self.assertEqual(_snapshot(self.asset), asset_before, "missing issue445 byte-equivalent asset denial contract")
        self.assertEqual(AssetAssignment.all_objects.filter(asset=self.asset).count(), assignment_count)
        self.assertEqual(AssetDisposal.all_objects.filter(asset=self.asset).count(), disposal_count)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), notification_count)
        self.assertEqual(
            ObjectChange.objects.filter(changed_object_type=asset_ct, changed_object_id=self.asset.pk).count(),
            change_count,
        )
        rendered = " ".join(logs.output)
        for value in (self.config["code"], str(self.tenant.pk), str(self.user.pk), str(self.job.pk)):
            self.assertIn(value, rendered, "missing issue445 safe bulk denial audit identifiers")
        self.assertNotIn(self.asset.asset_tag, rendered)
        denial_records = [record for record in logs.records if self.config["code"] in record.getMessage()]
        self.assertEqual(len(denial_records), 1, "missing issue445 one-record bulk denial audit contract")
        custom_fields = set(denial_records[0].__dict__) - STANDARD_LOG_RECORD_KEYS
        self.assertLessEqual(
            custom_fields,
            {"tenant_id", "actor_id", "job_id", "code"},
            "missing issue445 IDs-and-code-only bulk denial audit contract",
        )

    def test_revocation_after_enqueue_and_warm_cache_denies_before_domain_service(self):
        self._assert_denied(revoke_after_warm=True)

    def test_principal_missing_only_required_permission_denies(self):
        required = self.config["permission"]
        other_permissions = [
            permission for permission in ("assets.change_asset", "assets.add_assetdisposal") if permission != required
        ]
        self.user = User.objects.create_user(username=f"issue445-{self.family}-negative", password="pw")
        self.role = Role.objects.create(
            tenant=self.tenant,
            name=f"Issue445 {self.family} negative",
            permissions=other_permissions,
        )
        grant(self.user, self.tenant, self.role)
        self._assert_denied(revoke_after_warm=False)


class Issue445CheckinWorkerRBACTests(Issue445BulkWorkerRBACBase, TestCase):
    family = "checkin"


class Issue445CheckoutWorkerRBACTests(Issue445BulkWorkerRBACBase, TestCase):
    family = "checkout"


class Issue445DisposalWorkerRBACTests(Issue445BulkWorkerRBACBase, TestCase):
    family = "disposal"
