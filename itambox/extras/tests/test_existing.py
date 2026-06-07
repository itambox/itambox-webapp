from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.db import IntegrityError
from extras.models import Tag, CustomField, CustomFieldset, Dashboard

User = get_user_model()


class TagModelTests(TestCase):
    def test_tag_creation(self):
        tag = Tag.objects.create(name='Production', slug='production', color='00ff00')
        self.assertEqual(str(tag), 'Production')
        self.assertEqual(tag.color, '00ff00')
        self.assertEqual(tag.description, '')

    def test_tag_absolute_url(self):
        tag = Tag.objects.create(name='Staging', slug='staging')
        url = tag.get_absolute_url()
        self.assertIn(str(tag.pk), url)

    def test_tag_name_unique(self):
        Tag.objects.create(name='Critical', slug='critical')
        with self.assertRaises(IntegrityError):
            Tag.objects.create(name='Critical', slug='critical-dup')

    def test_tag_slug_unique(self):
        Tag.objects.create(name='Tag A', slug='tag-a')
        with self.assertRaises(IntegrityError):
            Tag.objects.create(name='Tag B', slug='tag-a')

    def test_tag_ordering(self):
        Tag.objects.create(name='B', slug='b')
        Tag.objects.create(name='A', slug='a')
        tags = list(Tag.objects.all())
        self.assertEqual(tags[0].name, 'A')

    def test_tag_with_description(self):
        tag = Tag.objects.create(
            name='DevOps', slug='devops', description='DevOps team assets', color='ff0000'
        )
        self.assertEqual(tag.description, 'DevOps team assets')


class CustomFieldModelTests(TestCase):
    def test_custom_field_creation(self):
        cf = CustomField.objects.create(
            name='cost_center', label='Cost Center', field_type=CustomField.FIELD_TYPE_TEXT
        )
        self.assertEqual(str(cf), 'Cost Center (Text)')
        self.assertFalse(cf.required)

    def test_custom_field_absolute_url(self):
        cf = CustomField.objects.create(name='dept', label='Department', field_type='text')
        url = cf.get_absolute_url()
        self.assertIn(str(cf.pk), url)

    def test_custom_field_types(self):
        for ft, ft_label in CustomField.FIELD_TYPE_CHOICES:
            cf = CustomField.objects.create(
                name=f'test_{ft}', label=f'Test {ft_label}', field_type=ft
            )
            self.assertEqual(cf.field_type, ft)

    def test_custom_field_required(self):
        cf = CustomField.objects.create(
            name='mandatory_field', label='Mandatory', field_type='text', required=True
        )
        self.assertTrue(cf.required)

    def test_custom_field_choices_for_select(self):
        cf = CustomField.objects.create(
            name='env', label='Environment', field_type=CustomField.FIELD_TYPE_SELECT,
            choices='Production\nStaging\nDevelopment'
        )
        self.assertEqual(cf.choices, 'Production\nStaging\nDevelopment')

    def test_custom_field_name_is_slug(self):
        cf = CustomField.objects.create(
            name='my_custom_field', label='My Custom Field', field_type='text'
        )
        self.assertEqual(cf.name, 'my_custom_field')


class CustomFieldsetModelTests(TestCase):
    def test_custom_fieldset_creation(self):
        cf1 = CustomField.objects.create(name='field_a', label='Field A', field_type='text')
        cf2 = CustomField.objects.create(name='field_b', label='Field B', field_type='number')
        cfs = CustomFieldset.objects.create(name='Asset Details')
        cfs.fields.add(cf1, cf2)
        self.assertEqual(str(cfs), 'Asset Details')
        self.assertEqual(cfs.fields.count(), 2)

    def test_custom_fieldset_absolute_url(self):
        cfs = CustomFieldset.objects.create(name='Server Config')
        url = cfs.get_absolute_url()
        self.assertIn(str(cfs.pk), url)

    def test_custom_fieldset_name_unique(self):
        CustomFieldset.objects.create(name='Unique Set')
        with self.assertRaises(IntegrityError):
            CustomFieldset.objects.create(name='Unique Set')

    def test_custom_fieldset_empty_fields(self):
        cfs = CustomFieldset.objects.create(name='Empty Set')
        self.assertEqual(cfs.fields.count(), 0)


class DashboardModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dashuser', password='testpass')

    def test_dashboard_creation(self):
        dash = Dashboard.objects.create(user=self.user)
        self.assertIn('Dashboard for dashuser', str(dash))
        self.assertEqual(dash.layout, [])

    def test_dashboard_add_widget(self):
        dash = Dashboard.objects.create(user=self.user)
        dash.add_widget('asset_count', title='Total Assets', w=4, h=2)
        self.assertEqual(len(dash.layout), 1)
        self.assertEqual(dash.layout[0]['widget'], 'asset_count')
        self.assertEqual(dash.layout[0]['title'], 'Total Assets')

    def test_dashboard_remove_widget(self):
        dash = Dashboard.objects.create(user=self.user)
        dash.add_widget('widget_a', title='Widget A')
        dash.add_widget('widget_b', title='Widget B')
        self.assertEqual(len(dash.layout), 2)
        dash.remove_widget(0)
        self.assertEqual(len(dash.layout), 1)
        self.assertEqual(dash.layout[0]['widget'], 'widget_b')

    def test_dashboard_update_widget(self):
        dash = Dashboard.objects.create(user=self.user)
        dash.add_widget('configurable', title='Original Title')
        dash.update_widget(0, title='Updated Title', visible=False)
        self.assertEqual(dash.layout[0]['title'], 'Updated Title')
        self.assertFalse(dash.layout[0]['visible'])

    def test_dashboard_move_widget(self):
        dash = Dashboard.objects.create(user=self.user)
        dash.add_widget('first', title='First')
        dash.add_widget('second', title='Second')
        dash.add_widget('third', title='Third')
        dash.move_widget(0, 2)
        self.assertEqual(dash.layout[0]['widget'], 'second')
        self.assertEqual(dash.layout[1]['widget'], 'third')
        self.assertEqual(dash.layout[2]['widget'], 'first')

    def test_dashboard_multiple_allowed(self):
        dash1 = Dashboard.objects.create(user=self.user, name="Dashboard 1")
        dash2 = Dashboard.objects.create(user=self.user, name="Dashboard 2")
        self.assertEqual(Dashboard.objects.filter(user=self.user).count(), 2)


class TagViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.tag = Tag.objects.create(name='Production', slug='production', color='00ff00')

    def test_list_view(self):
        url = reverse('extras:tag_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Production')

    def test_detail_view(self):
        url = reverse('extras:tag_detail', kwargs={'pk': self.tag.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Production')

    def test_create_view_get(self):
        url = reverse('extras:tag_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('extras:tag_create')
        response = self.client.post(url, {
            'name': 'Development',
            'slug': 'development',
            'color': '0000ff',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Tag.objects.filter(name='Development').exists())

    def test_edit_view_get(self):
        url = reverse('extras:tag_update', kwargs={'pk': self.tag.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('extras:tag_update', kwargs={'pk': self.tag.pk})
        response = self.client.post(url, {
            'name': 'Staging',
            'slug': 'staging',
            'color': 'ffff00',
        })
        self.assertEqual(response.status_code, 302)
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.name, 'Staging')

    def test_delete_view_get(self):
        url = reverse('extras:tag_delete', kwargs={'pk': self.tag.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse('extras:tag_delete', kwargs={'pk': self.tag.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Tag.objects.filter(pk=self.tag.pk).exists())


class CustomFieldViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.cf = CustomField.objects.create(
            name='dept_code', label='Department Code', field_type=CustomField.FIELD_TYPE_TEXT
        )

    def test_list_view(self):
        url = reverse('assets:customfield_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('assets:customfield_detail', kwargs={'pk': self.cf.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Department Code')

    def test_create_view_post(self):
        url = reverse('assets:customfield_create')
        response = self.client.post(url, {
            'name': 'building_floor',
            'label': 'Building Floor',
            'field_type': CustomField.FIELD_TYPE_NUMBER,
            'required': 'on',
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(CustomField.objects.filter(name='building_floor').exists())

    def test_delete_view_post(self):
        url = reverse('assets:customfield_delete', kwargs={'pk': self.cf.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CustomField.objects.filter(pk=self.cf.pk).exists())


class CustomFieldsetViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.cfs = CustomFieldset.objects.create(name='Network Config')

    def test_list_view(self):
        url = reverse('assets:customfieldset_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('assets:customfieldset_detail', kwargs={'pk': self.cfs.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Network Config')

    def test_create_view_post(self):
        url = reverse('assets:customfieldset_create')
        response = self.client.post(url, {
            'name': 'Server Specs',
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(CustomFieldset.objects.filter(name='Server Specs').exists())

    def test_delete_view_post(self):
        url = reverse('assets:customfieldset_delete', kwargs={'pk': self.cfs.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CustomFieldset.objects.filter(pk=self.cfs.pk).exists())


class TagColumnTests(TestCase):
    def test_tag_column_rendering(self):
        from extras.tables import TagColumn

        t1 = Tag.objects.create(name='Tag1', slug='tag1', color='ffffff')  # white bg -> dark text
        t2 = Tag.objects.create(name='Tag2', slug='tag2', color='000000')  # black bg -> white text
        t3 = Tag.objects.create(name='Tag3', slug='tag3', color='20c997')
        t4 = Tag.objects.create(name='Tag4', slug='tag4', color='111111')

        column = TagColumn(url_name='extras:tag_list')

        # Test empty tags
        self.assertEqual(column.render(Tag.objects.none()), "")

        # Test up to 3 tags
        qs_3 = Tag.objects.filter(name__in=['Tag1', 'Tag2', 'Tag3'])
        html_3 = column.render(qs_3)
        self.assertIn('background-color: #ffffff', html_3)
        self.assertIn('color: #212529', html_3)  # contrast text for white bg
        self.assertIn('background-color: #000000', html_3)
        self.assertIn('color: #ffffff', html_3)  # contrast text for black bg
        self.assertNotIn('+1', html_3)

        # Test 4 tags (limit exceeded)
        qs_4 = Tag.objects.all()
        html_4 = column.render(qs_4)
        self.assertIn('+1', html_4)


class BulkEditFormTests(TestCase):
    def test_bulk_edit_form_choices_and_styling(self):
        from django import forms
        from core.forms import BulkEditForm
        from organization.models import Location

        # Construct form with Location model
        form = BulkEditForm(model=Location)

        # Check 'status' field (it has choices on the model)
        self.assertIn('status', form.fields)
        self.assertIsInstance(form.fields['status'], forms.ChoiceField)
        
        # Check that it includes the STATUS_CHOICES plus '---------'
        status_choices = dict(form.fields['status'].choices)
        self.assertIn('planned', status_choices)
        self.assertIn('active', status_choices)
        self.assertEqual(status_choices[''], '---------')

        # Check that appropriate styling classes are added to widgets
        # 'status' (Select widget) should have 'form-select' class
        status_class = form.fields['status'].widget.attrs.get('class', '')
        self.assertIn('form-select', status_class)
        self.assertIn('data-tom-select', form.fields['status'].widget.attrs)

        # 'name' (TextInput widget) should have 'form-control' class
        name_class = form.fields['name'].widget.attrs.get('class', '')
        self.assertIn('form-control', name_class)

        # 'description' (Textarea widget) should have 'form-control' class
        desc_class = form.fields['description'].widget.attrs.get('class', '')
        self.assertIn('form-control', desc_class)


