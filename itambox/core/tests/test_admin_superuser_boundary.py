from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class AdminSuperuserBoundaryTests(TestCase):
    def test_staff_user_cannot_enter_admin_or_user_admin(self):
        staff_user = User.objects.create_user(
            username='staff-admin-boundary',
            password='password',
            is_staff=True,
        )
        self.client.force_login(staff_user)

        for url in (
            reverse('admin:index'),
            reverse('admin:users_user_changelist'),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('admin:login'), response.url)

    def test_superuser_can_enter_admin(self):
        superuser = User.objects.create_superuser(
            username='superuser-admin-boundary',
            password='password',
        )
        self.client.force_login(superuser)

        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('admin:users_user_changelist')).status_code,
            200,
        )
