"""Report and reconcile pre-upgrade fulfilment pledges that can never be attributed.

Before the Stable promotion, a partial receipt set every linked request to approved
without tracking delivered quantities. Those requests keep their historical approval
and their links keep a blank ``qty_received``; the receipt ledger deliberately never
attributes quantities to an already-approved request (no fabricated history), so such
pledges can stay open forever.

Only records that demonstrably match that defect signature are reconciliation
candidates: a pledge on a non-serialised line that was only partially received
while the tracked quantity is blank and the line's recorded received quantity
cannot cover the pledge's own allocation. Records that carry delivery evidence (an
assigned asset or a fully received line) are completed history and stay untouched;
pledges the received units could cover in full, lines without any receipt, and
serialised lines without an assigned asset require explicit operator review - the
command never closes them. Dry-run by default; ``--apply`` only softly closes the
candidate pledges. Request states, recorded quantities, and stock stay exactly as
recorded, and the affected units can simply be re-requested if they are still
needed.
"""

from django.core.management.base import BaseCommand

from assets.choices import RequestStatusChoices
from procurement.models import FulfillmentLink

CANDIDATE = "candidate"
REVIEW = "review"
COMPLETED = "completed"


def _classify(link):
    """Classify an untracked legacy pledge by the delivery evidence it still carries."""
    request = link.asset_request
    line = link.purchase_order_line
    if request.asset_id is not None:
        # A materialised, assigned asset proves the serialised delivery happened.
        return COMPLETED
    if line.asset_type_id is not None:
        # A serialised line always materialises assets; without one there is not enough
        # evidence to call the pledge dead.
        return REVIEW
    received = line.qty_received or 0
    if received >= line.qty_ordered:
        # The order was received in full; the approval cannot be shown to be premature.
        return COMPLETED
    if received == 0:
        # Nothing was ever received on the line, so the approval did not come from a receipt.
        return REVIEW
    if received >= link.qty_allocated:
        # The received units on the shared line could cover this pledge in full; with per-link
        # attribution untracked, it is not demonstrably undelivered.
        return REVIEW
    # The line's received quantity cannot cover this pledge's own allocation: the demonstrable
    # signature of the pre-upgrade blanket approval.
    return CANDIDATE


class Command(BaseCommand):
    help = (
        "Report fulfilment links whose request was approved before any tracked receipt "
        "(pre-upgrade pledges). Only demonstrable candidates (pledges on partially received "
        "non-serialised lines whose own allocation exceeds the line's received quantity) can be "
        "softly closed with --apply; completed records stay untouched and ambiguous ones require "
        "explicit operator review. Historical quantities and approval states are never rewritten."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Close (release) the demonstrable legacy-candidate pledges. The default is a dry-run report.",
        )

    def _report(self, link, bucket):
        line = link.purchase_order_line
        base = "link=%s tenant=%s po=%s line=%s request=%s" % (
            link.pk,
            link.tenant.slug if link.tenant_id else "-",
            line.purchase_order.order_number,
            line.pk,
            link.asset_request_id,
        )
        if bucket == CANDIDATE:
            self.stdout.write(
                "Legacy pledge: %s allocated=%s line-received=%s (the request keeps its recorded "
                "approval; new receipts are never attributed to it)." % (base, link.qty_allocated, line.qty_received)
            )
        elif bucket == REVIEW:
            self.stdout.write(
                "Needs operator review: %s (no sufficient delivery evidence on record; the command "
                "never closes this pledge)." % base
            )
        else:
            self.stdout.write("Completed record stays untouched: %s (delivery evidence on record)." % base)

    def handle(self, *args, **options):
        links = list(
            FulfillmentLink._base_manager.filter(
                deleted_at__isnull=True,
                qty_received__isnull=True,
                asset_request__deleted_at__isnull=True,
                asset_request__status=RequestStatusChoices.APPROVED,
            )
            .select_related(
                "asset_request",
                "purchase_order_line",
                "purchase_order_line__purchase_order",
                "tenant",
            )
            .order_by("pk")
        )
        if not links:
            self.stdout.write("No legacy pledges require reconciliation.")
            return

        buckets = {CANDIDATE: [], REVIEW: [], COMPLETED: []}
        for link in links:
            bucket = _classify(link)
            buckets[bucket].append(link)
            self._report(link, bucket)

        if buckets[REVIEW]:
            self.stdout.write(
                "%s record(s) need explicit operator review; this command never closes them." % len(buckets[REVIEW])
            )
        if buckets[COMPLETED]:
            self.stdout.write("%s completed record(s) stay untouched." % len(buckets[COMPLETED]))

        if not buckets[CANDIDATE]:
            self.stdout.write("No closable legacy pledges found.")
            return

        if not options["apply"]:
            self.stdout.write(
                "%s legacy pledge(s) found. Dry run: re-run with --apply to close them. "
                "Re-request the affected units if they are still needed." % len(buckets[CANDIDATE])
            )
            return

        for link in buckets[CANDIDATE]:
            link.delete()
        self.stdout.write("%s legacy pledge(s) closed." % len(buckets[CANDIDATE]))
