from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from assets.models import Manufacturer
from assets.views.manufacturer_views import ManufacturerCloneView

User = get_user_model()


class ObjectCloneDeferredSaveTests(TestCase):
    """Cloning must NOT persist on GET: the user gets a prefilled form and the
    record is created only on submit."""

    def test_get_object_returns_unsaved_prefilled_copy(self):
        original = Manufacturer.objects.create(name="Acme", slug="acme")

        view = ManufacturerCloneView()
        view.kwargs = {'pk': original.pk}
        cloned = view.get_object()

        # Unsaved, prefilled, and nothing was written to the DB.
        self.assertIsNone(cloned.pk)
        self.assertEqual(cloned.name, "Acme (Copy)")
        self.assertEqual(cloned.slug, "")
        self.assertEqual(Manufacturer.objects.count(), 1)
        self.assertEqual(view.original_object, original)

    def test_m2m_seeded_into_form_initial(self):
        from extras.models import Tag
        original = Manufacturer.objects.create(name="Globex", slug="globex")
        tag = Tag.objects.create(name="vip", slug="vip")
        original.tags.add(tag)

        view = ManufacturerCloneView()
        view.kwargs = {'pk': original.pk}
        view.object = view.get_object()

        initial = view.get_initial()
        self.assertIn('tags', initial)
        self.assertEqual(list(initial['tags']), [tag.pk])
        # Still nothing persisted.
        self.assertEqual(Manufacturer.objects.count(), 1)


class ObjectCloneHttpFlowTests(TestCase):
    """End-to-end: GET shows a prefilled form (no write); POST creates."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='cloner', password='pw', is_superuser=True
        )
        self.client.force_login(self.user)

    def test_clone_get_renders_form_and_persists_nothing(self):
        original = Manufacturer.objects.create(name="Acme", slug="acme")
        resp = self.client.get(reverse('assets:manufacturer_clone', kwargs={'pk': original.pk}))

        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'<form', resp.content)
        self.assertIn(b'Acme (Copy)', resp.content)   # prefilled
        self.assertEqual(Manufacturer.objects.count(), 1)  # NOT cloned yet

    def test_clone_post_creates_record(self):
        original = Manufacturer.objects.create(name="Acme", slug="acme")
        resp = self.client.post(
            reverse('assets:manufacturer_clone', kwargs={'pk': original.pk}),
            {'name': 'Acme Copy', 'slug': 'acme-copy', 'description': 'dup'},
        )

        self.assertIn(resp.status_code, (200, 302))
        self.assertEqual(Manufacturer.objects.count(), 2)
        self.assertTrue(Manufacturer.objects.filter(name='Acme Copy', slug='acme-copy').exists())


class ActionsColumnCloneButtonTests(TestCase):
    """The generic table actions column adds a ghost clone button for any model
    with a clone view, and omits it otherwise."""

    def test_clone_button_rendered_for_cloneable_model(self):
        from core.tables.columns import ActionsColumn
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")

        html = ActionsColumn().render(mfr, table=None)

        self.assertIn('mdi-content-copy', html)
        self.assertIn('btn-action', html)
        self.assertIn(f'/clone/', html)

    def test_clone_button_omitted_for_non_cloneable_model(self):
        from organization.models import Tenant
        from core.tables.columns import ActionsColumn
        tenant = Tenant.objects.create(name="Acme Inc", slug="acme-inc")

        html = ActionsColumn().render(tenant, table=None)

        self.assertNotIn('mdi-content-copy', html)
