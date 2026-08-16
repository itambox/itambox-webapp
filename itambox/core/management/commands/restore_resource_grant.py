"""Restore one resource grant after an explicit deadline correction."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from organization.models import Tenant
from organization.services.resource_grants import restore_resource_grant


class Command(BaseCommand):
    help = "Restore one revoked tenant resource grant with a corrected or cleared deadline."

    def add_arguments(self, parser):
        parser.add_argument("--grant", type=int, required=True, help="The grant primary key.")
        parser.add_argument("--tenant", type=int, required=True, help="The grant owner tenant primary key.")
        parser.add_argument("--user", type=int, required=True, help="The human operator primary key.")
        deadline = parser.add_mutually_exclusive_group(required=True)
        deadline.add_argument("--valid-until", help="A future ISO-8601 deadline.")
        deadline.add_argument("--clear-deadline", action="store_true", help="Restore as a perpetual grant.")
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirm this named single-row restore operation.",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Refusing to restore without explicit --confirm.")
        tenant = Tenant._base_manager.filter(pk=options["tenant"]).first()
        if tenant is None:
            raise CommandError("The specified tenant could not be resolved.")
        user = get_user_model()._base_manager.filter(pk=options["user"]).first()
        if user is None:
            raise CommandError("The specified operator could not be resolved.")

        valid_until = None
        if options["valid_until"]:
            valid_until = parse_datetime(options["valid_until"])
            if valid_until is None:
                raise CommandError("--valid-until must be a valid ISO-8601 datetime.")
            if valid_until.tzinfo is None or valid_until.utcoffset() is None:
                raise CommandError("--valid-until must include a timezone offset.")

        try:
            restore_resource_grant(
                grant_id=options["grant"],
                tenant_id=tenant.pk,
                user_id=user.pk,
                valid_until=valid_until,
            )
        except (PermissionDenied, ValidationError) as exc:
            raise CommandError("The resource grant could not be restored.") from exc
        self.stdout.write(self.style.SUCCESS(f"Restored resource grant {options['grant']}."))
