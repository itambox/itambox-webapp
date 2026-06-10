"""Regression test: AssetTagSequence.next_tag() must be safe under concurrency.

Before the select_for_update fix, two concurrent saves could format the same
tag from a stale read and collide on the asset_tag unique constraint.
"""
import threading

import pytest
from django.db import connection

from assets.models import AssetTagSequence


@pytest.mark.django_db(transaction=True)
def test_next_tag_is_unique_under_concurrent_claims():
    seq = AssetTagSequence.all_objects.create(prefix='CONC-', next_value=1, zero_padding=4)

    claimed = []
    claimed_lock = threading.Lock()
    barrier = threading.Barrier(4)

    def claim(n):
        try:
            barrier.wait(timeout=10)
            local = AssetTagSequence.all_objects.get(pk=seq.pk)
            for _ in range(n):
                tag = local.next_tag()
                with claimed_lock:
                    claimed.append(tag)
        finally:
            connection.close()

    threads = [threading.Thread(target=claim, args=(5,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(claimed) == 20
    assert len(set(claimed)) == 20, f"Duplicate tags claimed: {sorted(claimed)}"

    seq.refresh_from_db()
    assert seq.next_value == 21
