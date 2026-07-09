"""Phase 0 hardening tests for itambox/itambox/views/features.py.

Covers:
- G2: open-redirect guard on JournalEntryCreateView (user-supplied return_url
  pointing at a foreign host must NOT be honoured).
- G3: CSV formula-injection neutralisation (csv_safe helper).
"""
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from assets.models import Manufacturer
from core.csv_utils import csv_safe

User = get_user_model()


class JournalEntryRedirectTests(TestCase):
    """G2: a malicious return_url must fall back to the object's own URL."""

    def setUp(self):
        # Superuser so _check_attachment_parent_access's change-perm check passes.
        self.user = User.objects.create_superuser(
            username='redir-admin', email='redir-admin@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.manufacturer = Manufacturer.objects.create(
            name='Redirect Mfr', slug='redirect-mfr'
        )
        self.url = reverse(
            'journal_entry_add',
            kwargs={
                'app_label': 'assets',
                'model_name': 'manufacturer',
                'object_id': self.manufacturer.pk,
            },
        )

    def test_evil_return_url_is_not_honoured(self):
        evil = 'https://evil.example/x'
        response = self.client.post(
            self.url, {'comment': 'hello', 'return_url': evil}
        )
        self.assertEqual(response.status_code, 302)
        location = response['Location']
        # The cross-host target must be rejected; we fall back to a same-host URL.
        self.assertNotIn('evil.example', location)
        self.assertEqual(location, self.manufacturer.get_absolute_url())

    def test_same_host_return_url_is_preserved(self):
        safe = '/assets/manufacturers/'
        response = self.client.post(
            self.url, {'comment': 'hi', 'return_url': safe}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], safe)


class CsvSafeTests(TestCase):
    """G3: cells starting with a spreadsheet formula trigger are quoted."""

    def test_formula_leading_chars_are_prefixed(self):
        for trigger in ('=', '+', '-', '@', '\t', '\r'):
            value = trigger + 'CMD()'
            self.assertEqual(csv_safe(value), "'" + value)

    def test_equals_cell_is_quoted(self):
        self.assertEqual(csv_safe('=SUM(A1:A2)'), "'=SUM(A1:A2)")

    def test_plain_value_unchanged(self):
        self.assertEqual(csv_safe('plain text'), 'plain text')

    def test_none_becomes_empty_string(self):
        self.assertEqual(csv_safe(None), '')

    def test_non_string_is_stringified(self):
        self.assertEqual(csv_safe(42), '42')
