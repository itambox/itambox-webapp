from django.core.management.base import BaseCommand

from extras.models import EventRule


class Command(BaseCommand):
    help = "Report active event rules whose conditions are withdrawn for 1.0."

    def handle(self, *args, **options):
        withdrawn_rules = (
            rule
            for rule in EventRule.objects.select_related("tenant").order_by("name", "pk")
            if rule.conditions_withdrawn
        )
        count = 0
        for rule in withdrawn_rules:
            tenant = rule.tenant.slug if rule.tenant_id else "(system-wide)"
            self.stdout.write(
                f"pk={rule.pk} name={rule.name} tenant={tenant} action_type={rule.action_type} "
                f"enabled={rule.enabled} updated_at={rule.updated_at.isoformat()}"
            )
            count += 1

        self.stdout.write(f"{count} rule(s) have withdrawn conditions. These rules will not dispatch in 1.0.")
