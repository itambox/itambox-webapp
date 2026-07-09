from django.test import SimpleTestCase, RequestFactory

from extras.dashboard.widgets import NoteWidget


class NoteWidgetSchemeTests(SimpleTestCase):
    """WS4-4: NoteWidget markdown must not emit javascript:/data:/vbscript: hrefs."""

    def setUp(self):
        self.rf = RequestFactory()

    def _html(self, content):
        widget = NoteWidget(config={'config': {'content': content}})
        return str(widget.get_context(self.rf.get('/'))['content_html'])

    def test_dangerous_link_schemes_are_neutralized(self):
        for payload in (
            '[x](javascript:alert(1))',
            '[y](data:text/html,<script>x</script>)',
            '[z](vbscript:msgbox(1))',
        ):
            html = self._html(payload).lower()
            self.assertNotIn('javascript:', html)
            self.assertNotIn('data:text/html', html)
            self.assertNotIn('vbscript:', html)

    def test_safe_links_survive(self):
        html = self._html('[ok](https://example.com/page)')
        self.assertIn('https://example.com/page', html)
