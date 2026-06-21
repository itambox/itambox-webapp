"""WS2-10 regression: license seat checkout/checkin must emit a changelog entry.

`checkout_license`/`checkin_license_seat` record a human-readable changelog entry
("Checked out/in seat …") for the seat transaction. The license row itself does not
change, so the previous implementation triggered the entry with a no-op
`lic.save(update_fields=[])` — which ChangeLoggingMixin short-circuits whenever
prechange == postchange, silently dropping the audit entry. The fix emits the entry
directly via `lic._log_change(...)`, which is not subject to that equality
short-circuit.

These tests fail before the fix (no update ObjectChange for the license is created)
and pass after it.
"""

import uuid

from django.test import TestCase
from model_bakery import baker

from core.models import ObjectChange
from core.choices import ObjectChangeActionChoices
from core.tests.mixins import TenantTestMixin
from itambox.middleware import _request_id, _current_user
from licenses.models import License, LicenseTypeChoices
from licenses.services import checkout_license, checkin_license_seat
from software.models import Software
from organization.models import AssetHolder


class LicenseSeatCheckoutChangelogTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Seat Tenant', slug='seat-tenant')
        with self.tenant_context(self.tenant):
            self.software = baker.make(
                Software, name='Seat App', manufacturer__name='Seat Mfr', tenant=self.tenant
            )
            self.license = baker.make(
                License,
                software=self.software,
                tenant=self.tenant,
                seats=5,
                license_type=LicenseTypeChoices.PERPETUAL_SEAT,
            )
            self.holder = AssetHolder.objects.create(
                first_name='Seat', last_name='Holder', upn='seat.holder', tenant=self.tenant
            )

        # Change logging only fires when both contextvars are populated (mirrors a
        # live request); the autouse conftest fixture clears them after each test.
        _current_user.set(self.tenant_admin)
        _request_id.set(uuid.uuid4())

    def tearDown(self):
        super().tearDown()
        _current_user.set(None)
        _request_id.set(None)

    def _license_update_changes(self):
        # _base_manager: assert independently of the active tenant scope/soft-delete.
        return ObjectChange._base_manager.filter(
            changed_object_id=self.license.pk,
            object_type_repr='licenses | license',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )

    def test_checkout_emits_changelog_entry(self):
        before = self._license_update_changes().count()

        with self.tenant_context(self.tenant):
            checkout_license(self.license, assigned_holder=self.holder)

        changes = self._license_update_changes()
        self.assertEqual(
            changes.count(),
            before + 1,
            "checkout_license must emit exactly one 'update' ObjectChange for the license",
        )
        change = changes.latest('time')
        # It is the message-only entry the short-circuit used to drop: nothing on the
        # license row changed, so prechange == postchange.
        self.assertEqual(change.prechange_data, change.postchange_data)
        self.assertEqual(change.tenant_id, self.tenant.pk)

    def test_checkin_emits_changelog_entry(self):
        with self.tenant_context(self.tenant):
            assignment = checkout_license(self.license, assigned_holder=self.holder)

        before = self._license_update_changes().count()

        with self.tenant_context(self.tenant):
            checkin_license_seat(assignment)

        self.assertEqual(
            self._license_update_changes().count(),
            before + 1,
            "checkin_license_seat must emit exactly one 'update' ObjectChange for the license",
        )
