"""Regression tests for audit findings WS6-10 and WS6-12 (assets app).

WS6-10: the first-ever *global* AssetTagSequence is materialised via a
non-atomic get_or_create; two concurrent first-ever blank-tag no-tenant assets
could both take the create branch and collide on the unique_global_prefix
constraint -> IntegrityError. The create is now wrapped so the loser of the race
re-selects the committed row.

WS6-12: AssetRequest auto-approval reads its thresholds from operator-authored
ConfigContext JSON. A malformed value (non-dict, or string/None thresholds) used
to flow straight into a `qty <= threshold` comparison and raise. The parser now
validates the shape and falls back to the sane defaults without raising.
"""
import threading

import pytest
from django.db import connection

from assets.models import AssetRequest, AssetTagSequence, Category, Manufacturer
from assets.choices import RequestStatusChoices
from organization.models import Location, Site, Tenant
from inventory.models import Accessory, AccessoryStock
from django.contrib.auth import get_user_model

User = get_user_model()


# ---------------------------------------------------------------------------
# WS6-10
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_first_ever_global_tag_generation_is_race_safe():
    """Two back-to-back first-ever global tag generations (no pre-existing global
    sequence) each yield a distinct ASSET-NNNNNN tag with no IntegrityError."""
    # Sanity: there must be no global default sequence to begin with.
    assert not AssetTagSequence.all_objects.filter(
        tenant__isnull=True, category__isnull=True, prefix='ASSET-'
    ).exists()

    class _StubAsset:
        tenant = None
        category = None

    tag1 = AssetTagSequence.get_next_tag_for_asset(_StubAsset())
    tag2 = AssetTagSequence.get_next_tag_for_asset(_StubAsset())

    assert tag1.startswith('ASSET-')
    assert tag2.startswith('ASSET-')
    assert tag1 != tag2, f"Expected distinct tags, got {tag1!r} and {tag2!r}"

    # Exactly one global default row materialised (no duplicate from the create branch).
    assert AssetTagSequence.all_objects.filter(
        tenant__isnull=True, category__isnull=True, prefix='ASSET-'
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_ever_global_default_does_not_integrityerror():
    """Concurrent first-ever global-default creations must not surface an
    IntegrityError from the unique_global_prefix constraint; the loser of the
    race re-selects the committed row."""
    assert not AssetTagSequence.all_objects.filter(
        tenant__isnull=True, category__isnull=True, prefix='ASSET-'
    ).exists()

    barrier = threading.Barrier(4)
    errors = []
    seqs = []
    lock = threading.Lock()

    def create_default():
        try:
            barrier.wait(timeout=10)
            seq = AssetTagSequence._get_or_create_global_default()
            with lock:
                seqs.append(seq.pk)
        except Exception as exc:  # pragma: no cover - failure path asserted below
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=create_default) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [], f"Unexpected errors from concurrent creation: {errors}"
    # All threads resolved to the same single global default row.
    assert len(set(seqs)) == 1
    assert AssetTagSequence.all_objects.filter(
        tenant__isnull=True, category__isnull=True, prefix='ASSET-'
    ).count() == 1


# ---------------------------------------------------------------------------
# WS6-12
# ---------------------------------------------------------------------------

class _AutoApprovalConfigBase:
    """Shared fixture: a tenant with a stocked accessory eligible for auto-approval."""

    def _setup(self):
        self.tenant = Tenant.objects.create(name="Acme", slug="acme-ws612")
        self.requester = User.objects.create_user(username='req-ws612', password='pw')
        self.manufacturer = Manufacturer.objects.create(name="Logitech", slug="logi-ws612")
        self.site = Site.objects.create(name="HQ", slug="hq-ws612", tenant=self.tenant)
        self.location = Location.objects.create(name="Store", slug="store-ws612", site=self.site, tenant=self.tenant)
        self.acc_cat = Category.objects.create(name="Acc", slug="acc-ws612", applies_to={"accessory": True})
        self.accessory = Accessory.objects.create(name="Mouse", manufacturer=self.manufacturer, category=self.acc_cat)
        AccessoryStock.objects.create(accessory=self.accessory, location=self.location, qty=10)

    def _make_config(self, value):
        # inline import: ConfigContext lives in extras; mirrors test_requests.py usage.
        from extras.models import ConfigContext
        cc = ConfigContext.objects.create(
            name="Auto Approval Settings",
            data={'requisition_auto_approval_thresholds': value},
            weight=100,
        )
        cc.tenants.add(self.tenant)
        return cc


@pytest.mark.django_db
class TestMalformedAutoApprovalConfig(_AutoApprovalConfigBase):
    """A malformed ConfigContext threshold value must fall back to defaults
    without raising — auto-approval is advisory, capacity is enforced at fulfilment."""

    def test_non_dict_override_falls_back_to_defaults(self):
        self._setup()
        # A list where a dict is expected: must not raise, and the default
        # accessory threshold of 3 still governs (qty 2 <= 3 -> auto-approved).
        self._make_config(["not", "a", "dict"])

        req = AssetRequest.objects.create(
            requester=self.requester, accessory=self.accessory, qty=2, tenant=self.tenant
        )
        assert req.status == RequestStatusChoices.APPROVED

    def test_string_threshold_values_fall_back_to_defaults(self):
        self._setup()
        # String values instead of ints would raise on `qty <= threshold`.
        self._make_config({'accessory': 'lots', 'consumable': 'tons'})

        # qty 2 <= default 3 -> auto-approved (the bad override is ignored).
        req = AssetRequest.objects.create(
            requester=self.requester, accessory=self.accessory, qty=2, tenant=self.tenant
        )
        assert req.status == RequestStatusChoices.APPROVED

    def test_none_threshold_value_falls_back_to_default(self):
        self._setup()
        # null in JSON -> None; comparison would raise without validation.
        self._make_config({'accessory': None})

        req = AssetRequest.objects.create(
            requester=self.requester, accessory=self.accessory, qty=2, tenant=self.tenant
        )
        assert req.status == RequestStatusChoices.APPROVED

    def test_bool_threshold_value_is_rejected(self):
        self._setup()
        # bool is an int subclass; True must not be accepted as a threshold of 1.
        # With the bad value ignored, the default of 3 applies -> qty 2 auto-approved.
        self._make_config({'accessory': True})

        req = AssetRequest.objects.create(
            requester=self.requester, accessory=self.accessory, qty=2, tenant=self.tenant
        )
        assert req.status == RequestStatusChoices.APPROVED

    def test_valid_int_override_still_applies(self):
        self._setup()
        # Regression guard: a well-formed override must still raise the threshold.
        self._make_config({'accessory': 5})

        # qty 4 > default 3 but <= override 5 -> auto-approved proves the override took effect.
        req = AssetRequest.objects.create(
            requester=self.requester, accessory=self.accessory, qty=4, tenant=self.tenant
        )
        assert req.status == RequestStatusChoices.APPROVED
