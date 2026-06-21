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
