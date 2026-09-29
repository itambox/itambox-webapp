"""Report and reconcile pre-upgrade fulfilment pledges that can never be attributed.

Before the Stable promotion, a partial receipt set every linked request to approved
without tracking delivered quantities. Those requests keep their historical approval
and their links keep a blank ``qty_received``; the receipt ledger deliberately never
attributes quantities to an already-approved request (no fabricated history), so such
pledges can stay open forever. This command names them explicitly and offers one
explicit resolution: dry-run by default, ``--apply`` softly closes (releases) the dead
pledge. Request states, recorded quantities, and stock stay exactly as recorded, and
the affected units can simply be re-requested if they are still needed.
"""

from django.core.management.base import BaseCommand

from assets.choices import RequestStatusChoices
from procurement.models import FulfillmentLink


class Command(BaseCommand):
    help = (
        "Report fulfilment links whose request was approved before any tracked receipt "
        "(pre-upgrade pledges). With --apply, softly close those dead pledges; historical "
        "quantities and approval states are never rewritten."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Close (release) the reported legacy pledges. The default is a dry-run report.",
        )

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

        for link in links:
            line = link.purchase_order_line
            self.stdout.write(
                "Legacy pledge: link=%s tenant=%s po=%s line=%s request=%s allocated=%s "
                "line-received=%s (the request keeps its recorded approval; new receipts are "
                "never attributed to it)."
                % (
                    link.pk,
                    link.tenant.slug if link.tenant_id else "-",
                    line.purchase_order.order_number,
                    line.pk,
                    link.asset_request_id,
                    link.qty_allocated,
                    line.qty_received,
                )
            )

        if not options["apply"]:
            self.stdout.write(
                "%s legacy pledge(s) found. Dry run: re-run with --apply to close them. "
                "Re-request the affected units if they are still needed." % len(links)
            )
            return

        for link in links:
            link.delete()
        self.stdout.write("%s legacy pledge(s) closed." % len(links))
