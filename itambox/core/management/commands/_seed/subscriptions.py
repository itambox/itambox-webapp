"""Subscriptions seed mixin: per-organization cloud/SaaS subscriptions.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.subscriptions import SeedSubscriptionsMixin

    class Command(SeedSubscriptionsMixin, BaseCommand):
        ...

``_seed_subscriptions`` must run after ``_seed_assets`` (it reads
``self._orgs`` / ``self._tenants`` / ``self._tenant_meta`` / ``self._providers``
/ ``self._provisioner`` / ``self._assets_by_tenant``). It populates
``self._subscriptions`` (consumed by the contracts/costing phase).
"""

import datetime
import random

from django.contrib.contenttypes.models import ContentType

TODAY = datetime.date.today()


def days_ago(n):
    return TODAY - datetime.timedelta(days=n)


def days_ahead(n):
    return TODAY + datetime.timedelta(days=n)


class SeedSubscriptionsMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _seed_subscriptions(self):
        from subscriptions.models import Subscription, SubscriptionAssignment
        from assets.models import Asset
        self.stdout.write('--- Subscriptions ---')
        self._subscriptions = []
        ct_asset = ContentType.objects.get_for_model(Asset)
        # One cloud footprint per organization, contracted centrally by the parent
        # entity (the group's primary tenant) and consumed across its sibling entities.
        x_entity = 0
        for org in self._orgs:
            primary_slug = org['tenants'][0]['slug']
            tenant = self._tenants[primary_slug]
            currency = self._tenant_meta[primary_slug]['currency']
            plan = [('Amazon Web Services', random.randint(30000, 150000)),
                    ('Microsoft Azure', random.randint(40000, 200000))]
            if org['kind'] == 'msp' or random.random() < 0.5:
                plan.append(('GitHub Enterprise', random.randint(4000, 40000)))
            if random.random() < 0.5:
                plan.append(('Datadog', random.randint(8000, 36000)))
            aws_sub = None
            for prov_name, cost in plan:
                start = days_ago(random.randint(60, 700))
                renewal = days_ahead(random.choice([20, 35, 60, 120, 300]))
                sub = Subscription.objects.create(
                    name=prov_name, provider=self._providers[prov_name], type='saas',
                    start_date=start, renewal_date=renewal, renewal_cost=cost, currency=currency,
                    billing_cycle='annual', term_months=12, auto_renewal=True,
                    contract_reference=f"MSA-{prov_name.split()[0].upper()}-{start.year}",
                    owner=self._provisioner,
                    description=f"{prov_name} cloud subscription — group contract held by {tenant.name}.",
                    tenant=tenant)
                self._subscriptions.append(sub)
                if prov_name == 'Amazon Web Services':
                    aws_sub = sub
            # Cross-entity consumption: assign the group AWS contract to servers in EVERY
            # entity of the group, not just the contracting one (shared-service realism).
            if aws_sub:
                for t in org['tenants']:
                    servers = [a for a in self._assets_by_tenant.get(t['slug'], [])
                               if a.asset_role and 'server' in a.asset_role.slug]
                    sibling = t['slug'] != primary_slug
                    for srv in servers[:3]:
                        _, made = SubscriptionAssignment.objects.get_or_create(
                            subscription=aws_sub, content_type=ct_asset, object_id=srv.pk,
                            defaults={'assigned_by': self._provisioner,
                                      'notes': ('Hybrid workload node (intercompany — billed to group contract)'
                                                if sibling else 'Hybrid workload node')})
                        if made and sibling:
                            x_entity += 1
        self.stdout.write(f'  {len(self._subscriptions)} subscriptions across {len(self._orgs)} organizations, '
                          f'{x_entity} cross-entity workload assignments.')
