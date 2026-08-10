"""Regression tests for audit finding WS6-10 (assets app).

WS6-10: the first-ever *global* AssetTagSequence is materialised via a
non-atomic get_or_create; two concurrent first-ever blank-tag no-tenant assets
could both take the create branch and collide on the unique_global_prefix
constraint -> IntegrityError. The create is now wrapped so the loser of the race
re-selects the committed row.

(WS6-12, which covered AssetRequest auto-approval thresholds sourced from
ConfigContext, was removed together with the ConfigContext feature.)
"""

import threading

import pytest
from django.db import connection

from assets.models import AssetTagSequence


@pytest.mark.django_db(transaction=True)
def test_first_ever_global_tag_generation_is_race_safe():
    """Two back-to-back first-ever global tag generations (no pre-existing global
    sequence) each yield a distinct ASSET-NNNNNN tag with no IntegrityError."""
    # Sanity: there must be no global default sequence to begin with.
    assert not AssetTagSequence.all_objects.filter(tenant__isnull=True, category__isnull=True, prefix="ASSET-").exists()

    class _StubAsset:
        tenant = None
        category = None

    tag1 = AssetTagSequence.get_next_tag_for_asset(_StubAsset())
    tag2 = AssetTagSequence.get_next_tag_for_asset(_StubAsset())

    assert tag1.startswith("ASSET-")
    assert tag2.startswith("ASSET-")
    assert tag1 != tag2, f"Expected distinct tags, got {tag1!r} and {tag2!r}"

    # Exactly one global default row materialised (no duplicate from the create branch).
    assert AssetTagSequence.all_objects.filter(tenant__isnull=True, category__isnull=True, prefix="ASSET-").count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.serial_only
def test_concurrent_first_ever_global_default_does_not_integrityerror():
    """Concurrent first-ever global-default creations must not surface an
    IntegrityError from the unique_global_prefix constraint; the loser of the
    race re-selects the committed row."""
    assert not AssetTagSequence.all_objects.filter(tenant__isnull=True, category__isnull=True, prefix="ASSET-").exists()

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
    assert AssetTagSequence.all_objects.filter(tenant__isnull=True, category__isnull=True, prefix="ASSET-").count() == 1


@pytest.mark.django_db
def test_global_default_fallback_retries_when_winner_row_not_yet_visible():
    """Regression #306: the IntegrityError fallback must retry the re-select
    instead of raising DoesNotExist when the winner's insert is not yet visible
    (READ COMMITTED, race inside an outer transaction.atomic() block — e.g. a
    custody accept on a fresh instance without a global sequence)."""
    from unittest import mock

    from django.db import IntegrityError

    winner = AssetTagSequence.all_objects.create(
        tenant=None, category=None, prefix="ASSET-", next_value=1, zero_padding=6, is_active=True
    )
    real_get = AssetTagSequence.all_objects.get
    state = {"attempts": 0}

    def racy_get_or_create(*args, **kwargs):
        raise IntegrityError("duplicate key value violates unique constraint")

    def delayed_get(*args, **kwargs):
        state["attempts"] += 1
        if state["attempts"] == 1:
            raise AssetTagSequence.DoesNotExist
        return real_get(*args, **kwargs)

    with (
        mock.patch.object(AssetTagSequence.all_objects, "get_or_create", side_effect=racy_get_or_create),
        mock.patch.object(AssetTagSequence.all_objects, "get", side_effect=delayed_get),
        mock.patch("time.sleep"),
    ):
        seq = AssetTagSequence._get_or_create_global_default()

    assert seq.pk == winner.pk
    assert state["attempts"] == 2, f"expected one retry, got {state['attempts']}"


@pytest.mark.django_db
def test_global_default_surfaces_does_not_exist_after_retry_bound():
    """The retry bound must not loop forever: when no winner row ever becomes
    visible, the real DoesNotExist is surfaced after the bounded retries."""
    from unittest import mock

    from django.db import IntegrityError

    state = {"attempts": 0}

    def racy_get_or_create(*args, **kwargs):
        raise IntegrityError("duplicate key value violates unique constraint")

    def never_visible(*args, **kwargs):
        state["attempts"] += 1
        raise AssetTagSequence.DoesNotExist

    with (
        mock.patch.object(AssetTagSequence.all_objects, "get_or_create", side_effect=racy_get_or_create),
        mock.patch.object(AssetTagSequence.all_objects, "get", side_effect=never_visible),
        mock.patch("time.sleep"),
    ):
        with pytest.raises(AssetTagSequence.DoesNotExist):
            AssetTagSequence._get_or_create_global_default()

    assert state["attempts"] == 6, f"expected 5 retries + final get, got {state['attempts']}"
