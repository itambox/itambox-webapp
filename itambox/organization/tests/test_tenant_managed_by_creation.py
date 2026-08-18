from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import grant
from organization.access import accessible_tenant_ids
from organization.forms import TenantForm
from organization.models import Role, RoleGrantScope, Tenant

User = get_user_model()


class TenantManagedByCreationTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username="provider-technician", is_staff=True)
        self.provider = self._create_provider("Northwind", "northwind")
        self._authorize(self.provider)
        self._login_at(self.provider)

    def _create_provider(self, name, slug):
        return Tenant.objects.create(name=name, slug=slug, is_provider=True)

    def _authorize(self, provider, *, actor=None):
        actor = actor or self.actor
        role = Role.objects.create(
            tenant=provider,
            name=f"Tenant creator for {provider.slug}",
            permissions=["organization.add_tenant"],
        )
        grant(actor, provider, role, granted_by=actor)

    def _login_at(self, tenant, *, actor=None):
        actor = actor or self.actor
        self.client.force_login(actor)
        session = self.client.session
        session["active_tenant_id"] = tenant.pk
        session.save()

    @staticmethod
    def _tenant_data(name="Managed customer", slug="managed-customer", **extra):
        return {
            "name": name,
            "slug": slug,
            "currency": "EUR",
            **extra,
        }

    def test_create_form_lists_only_live_authorized_root_providers(self):
        second_provider = self._create_provider("Contoso", "contoso")
        self._authorize(second_provider)
        foreign_provider = self._create_provider("Foreign", "foreign")
        deleted_provider = self._create_provider("Deleted", "deleted")
        self._authorize(deleted_provider)
        deleted_provider.delete()
        chained_provider = self._create_provider("Chained", "chained")
        Tenant._base_manager.filter(pk=chained_provider.pk).update(managed_by_id=self.provider.pk)
        self._authorize(chained_provider)

        form = TenantForm(user=self.actor)

        self.assertIn("managed_by", form.fields)
        self.assertTrue(form.fields["managed_by"].required)
        self.assertEqual(
            set(form.fields["managed_by"].queryset.values_list("pk", flat=True)),
            {self.provider.pk, second_provider.pk},
        )
        self.assertNotIn(foreign_provider.pk, form.fields["managed_by"].queryset.values_list("pk", flat=True))
        self.assertNotIn(deleted_provider.pk, form.fields["managed_by"].queryset.values_list("pk", flat=True))
        self.assertNotIn(chained_provider.pk, form.fields["managed_by"].queryset.values_list("pk", flat=True))

    def test_query_parameter_only_selects_an_authorized_initial_provider(self):
        foreign_provider = self._create_provider("Foreign", "foreign")

        authorized_form = TenantForm(user=self.actor, managed_by_param=str(self.provider.pk))
        unauthorized_form = TenantForm(user=self.actor, managed_by_param=str(foreign_provider.pk))

        self.assertEqual(authorized_form.fields["managed_by"].initial, self.provider.pk)
        self.assertIsNone(unauthorized_form.fields["managed_by"].initial)
        self.assertEqual(
            set(unauthorized_form.fields["managed_by"].queryset.values_list("pk", flat=True)),
            {self.provider.pk},
        )

    def test_managed_by_is_required_when_an_eligible_provider_exists(self):
        form = TenantForm(data=self._tenant_data(), user=self.actor)

        self.assertFalse(form.is_valid())
        self.assertIn("managed_by", form.errors)
        self.assertFalse(Tenant._base_manager.filter(slug="managed-customer").exists())

    def test_standalone_creation_remains_possible_without_an_eligible_provider(self):
        actor = User.objects.create_user(username="standalone-creator", is_staff=True)
        standalone = Tenant.objects.create(name="Standalone", slug="standalone")
        role = Role.objects.create(
            tenant=standalone,
            name="Standalone tenant creator",
            permissions=["organization.add_tenant"],
        )
        grant(actor, standalone, role, granted_by=actor)

        form = TenantForm(
            data=self._tenant_data(name="Root customer", slug="root-customer"),
            user=actor,
        )

        self.assertFalse(form.fields["managed_by"].required)
        self.assertTrue(form.is_valid(), form.errors)
        created = form.save()
        self.assertIsNone(created.managed_by_id)

    def test_tampered_managed_by_values_are_rejected_without_creation(self):
        foreign_provider = self._create_provider("Foreign", "foreign")
        non_provider = Tenant.objects.create(name="Ordinary tenant", slug="ordinary")
        deleted_provider = self._create_provider("Deleted", "deleted")
        self._authorize(deleted_provider)
        deleted_provider.delete()
        chained_provider = self._create_provider("Chained", "chained")
        Tenant._base_manager.filter(pk=chained_provider.pk).update(managed_by_id=self.provider.pk)
        self._authorize(chained_provider)

        invalid_targets = {
            "foreign": foreign_provider.pk,
            "non-provider": non_provider.pk,
            "deleted": deleted_provider.pk,
            "chained": chained_provider.pk,
        }
        for label, provider_id in invalid_targets.items():
            with self.subTest(provider=label):
                slug = f"tampered-{label.replace('-', '')}"
                form = TenantForm(
                    data=self._tenant_data(name=f"Tampered {label}", slug=slug, managed_by=provider_id),
                    user=self.actor,
                )

                self.assertFalse(form.is_valid())
                self.assertIn("managed_by", form.errors)
                self.assertFalse(Tenant._base_manager.filter(slug=slug).exists())

    def test_existing_non_superuser_cannot_change_managed_by_through_form(self):
        customer = Tenant.objects.create(
            name="Existing customer",
            slug="existing-customer",
            managed_by=self.provider,
        )
        other_provider = self._create_provider("Other", "other")
        self._authorize(other_provider)

        form = TenantForm(
            data=self._tenant_data(name="Renamed customer", slug=customer.slug, managed_by=other_provider.pk),
            instance=customer,
            user=self.actor,
        )

        self.assertNotIn("managed_by", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        customer.refresh_from_db()
        self.assertEqual(customer.managed_by_id, self.provider.pk)

    def test_normal_creation_with_managed_by_uses_onboarding_projection(self):
        response = self.client.post(
            reverse("organization:tenant_create"),
            self._tenant_data(managed_by=self.provider.pk),
        )

        self.assertEqual(response.status_code, 302)
        customer = Tenant._base_manager.get(slug="managed-customer")
        self.assertEqual(customer.managed_by_id, self.provider.pk)
        self.assertIn(customer.pk, accessible_tenant_ids(self.actor))
        self.assertTrue(
            RoleGrantScope._base_manager.filter(
                tenant=customer,
                scope_type=RoleGrantScope.SCOPE_TENANT,
                role_grant__membership__user=self.actor,
            ).exists()
        )

    def test_normal_creation_without_managed_by_cannot_silently_create_root_tenant(self):
        response = self.client.post(
            reverse("organization:tenant_create"),
            self._tenant_data(name="Accidental root", slug="accidental-root"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "managed_by")
        self.assertFalse(Tenant._base_manager.filter(slug="accidental-root").exists())
