from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, connection, transaction

from core.auth.oidc import (
    VerifiedOIDCIdentity,
    _acquire_oidc_identity_lock,
    _set_oidc_transaction_timeouts,
)
from core.auth.providers import _resolve_oidc_setting, is_usable_oidc_config
from core.oidc_identity import oidc_sensitive_audit, validate_oidc_identity
from core.tasks.context import TaskContext


def configured_oidc_issuers() -> set[str]:
    tenant_configs = getattr(settings, "ITAMBOX_TENANT_OIDC_CONFIGS", {})
    if not isinstance(tenant_configs, dict):
        tenant_configs = {}

    TenantModel = apps.get_model("organization", "Tenant")
    live_slugs = set(
        TenantModel._base_manager.filter(
            slug__in=[slug for slug in tenant_configs if isinstance(slug, str)],
            deleted_at__isnull=True,
        ).values_list("slug", flat=True)
    )
    issuers = set()
    for slug, config in tenant_configs.items():
        if slug not in live_slugs or not is_usable_oidc_config(config):
            continue
        value = _resolve_oidc_setting(config, "OIDC_OP_ISSUER")
        if isinstance(value, str) and value:
            issuers.add(value)

    if not tenant_configs and is_usable_oidc_config({}):
        value = _resolve_oidc_setting({}, "OIDC_OP_ISSUER")
        if isinstance(value, str) and value:
            issuers.add(value)
    return issuers


def validate_oidc_identity_input(issuer: object, subject: object) -> tuple[str, str]:
    try:
        validated_issuer, validated_subject = validate_oidc_identity(issuer, subject)
    except ValidationError:
        raise CommandError("The OIDC identity input is invalid.") from None
    if validated_issuer not in configured_oidc_issuers():
        raise CommandError("The OIDC issuer is not configured.")
    return validated_issuer, validated_subject


class Command(BaseCommand):
    help = "Bind one exact configured OIDC identity to an internal User."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            "--user-id",
            dest="user",
            type=int,
            required=True,
            help="The internal User primary key.",
        )
        parser.add_argument(
            "--issuer",
            required=True,
            help="The exact configured OIDC issuer.",
        )
        parser.add_argument(
            "--subject",
            required=True,
            help="The exact verified OIDC subject.",
        )
        parser.add_argument(
            "--operator",
            "--operator-user",
            dest="operator",
            type=int,
            help="Optional distinct operator User primary key for command context.",
        )
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and inspect the binding without writing.",
        )
        mode.add_argument(
            "--confirm",
            action="store_true",
            help="Commit the explicit binding if it is safe to do so.",
        )

    @staticmethod
    def _configured_issuers() -> set[str]:
        return configured_oidc_issuers()

    def _validate_input(self, issuer, subject):
        validate_oidc_identity_input(issuer, subject)

    @staticmethod
    def _identity_model():
        return apps.get_model("users", "OIDCIdentity")

    def _binding(self, issuer, subject, *, for_update=False):
        IdentityModel = self._identity_model()
        queryset = IdentityModel._base_manager.select_related("user")
        if for_update:
            queryset = queryset.select_for_update(of=("self",))
        return queryset.filter(issuer=issuer, subject=subject).first()

    def handle(self, *args, **options):
        issuer = options["issuer"]
        subject = options["subject"]
        user_id = options["user"]
        operator_id = options.get("operator")
        self._validate_input(issuer, subject)
        if operator_id is not None and operator_id == user_id:
            raise CommandError("The operator must be distinct from the target User.")

        UserModel = apps.get_model("users", "User")
        user = UserModel._base_manager.filter(pk=user_id).first()
        if user is None:
            raise CommandError("The target User could not be resolved.")

        if options["dry_run"]:
            binding = self._binding(issuer, subject)
            if binding is None:
                self.stdout.write(f"Would create OIDC identity binding for User #{user.pk}.")
            elif binding.user_id == user.pk:
                self.stdout.write(f"OIDC identity binding already exists for User #{user.pk}.")
            else:
                raise CommandError("The exact OIDC identity is already bound to another User.")
            return

        operator = None
        if operator_id is not None:
            operator = UserModel._base_manager.filter(pk=operator_id).first()
            if operator is None:
                raise CommandError("The operator User could not be resolved.")

        operation = "oidc.identity.bind"
        with TaskContext(user_id=operator.pk if operator else None, operation=operation):
            outcome = self._write_binding(user, issuer, subject)

        if outcome == "created":
            self.stdout.write(self.style.SUCCESS(f"Created OIDC identity binding for User #{user.pk}."))
        else:
            self.stdout.write(self.style.SUCCESS(f"OIDC identity binding already exists for User #{user.pk}."))

    def _write_binding(self, user, issuer, subject):
        IdentityModel = self._identity_model()
        with oidc_sensitive_audit():
            with transaction.atomic():
                with connection.cursor() as cursor:
                    _set_oidc_transaction_timeouts(cursor)
                    _acquire_oidc_identity_lock(
                        cursor,
                        VerifiedOIDCIdentity(issuer=issuer, subject=subject),
                    )

                binding = self._binding(issuer, subject, for_update=True)
                user_model = apps.get_model("users", "User")
                locked_user = user_model._base_manager.select_for_update().filter(pk=user.pk).first()
                if locked_user is None:
                    raise CommandError("The target User could not be resolved.")

                if binding is not None:
                    if binding.user_id != locked_user.pk:
                        raise CommandError("The exact OIDC identity is already bound to another User.")
                    return "existing"

                try:
                    with transaction.atomic():
                        IdentityModel.objects.create(
                            user=locked_user,
                            issuer=issuer,
                            subject=subject,
                        )
                except IntegrityError:
                    binding = self._binding(issuer, subject, for_update=True)
                    if binding is None or binding.user_id != locked_user.pk:
                        raise CommandError("The OIDC identity binding could not be reconciled safely.") from None
                    return "existing"
                return "created"
