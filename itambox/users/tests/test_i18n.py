from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()

class InternationalizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', password='testpass', is_staff=True, is_superuser=True
        )

    def test_language_selection_german(self):
        # Log in user
        self.client.force_login(self.user)
        # Select German language by sending POST to set_language
        url = reverse('set_language')
        response = self.client.post(url, {'language': 'de'}, follow=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify the cookie was set
        self.assertEqual(self.client.cookies.get('django_language').value, 'de')

    def test_german_translation_rendering(self):
        self.client.force_login(self.user)
        # Set language cookie to 'de'
        self.client.cookies['django_language'] = 'de'
        
        # Access the user profile page
        url = reverse('users:user_profile')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        # Verify that German strings are rendered in the response (e.g. "Benutzerprofil")
        self.assertContains(response, "Benutzerprofil")
        # Verify sidebar translations are rendered (e.g. "Organisation")
        self.assertContains(response, "Organisation")

    def test_german_flash_messages(self):
        self.client.force_login(self.user)
        self.client.cookies['django_language'] = 'de'
        
        # Trigger profile update to get translated success message
        url = reverse('users:user_profile')
        response = self.client.post(url, {
            'first_name': 'Rene',
            'last_name': 'Rettig',
            'email': 'rene.rettig@example.com'
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        # Verify the success message is translated to German
        self.assertContains(response, "Profil erfolgreich aktualisiert.")
