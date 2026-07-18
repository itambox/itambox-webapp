import json
import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from django.urls import reverse


class PWAInstallabilityTests(SimpleTestCase):
    def test_public_login_page_exposes_pwa_capabilities(self):
        response = self.client.get(reverse("login"))

        manifest_link = '<link rel="manifest" href="{}">'.format(reverse("manifest.json"))
        self.assertContains(response, manifest_link, html=True)
        self.assertContains(response, "navigator.serviceWorker.register")
        self.assertContains(response, reverse("service-worker.js"))

    def test_manifest_has_stable_identity_and_root_scope(self):
        response = self.client.get(reverse("manifest.json"))
        manifest = json.loads(response.content)

        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["scope"], "/")

    def test_manifest_declares_existing_installable_png_icons(self):
        response = self.client.get(reverse("manifest.json"))
        manifest = json.loads(response.content)

        icons_by_size = {icon["sizes"]: icon for icon in manifest["icons"] if icon["type"] == "image/png"}
        for size in ("192x192", "512x512"):
            with self.subTest(size=size):
                icon = icons_by_size[size]
                self.assertIn("any", icon.get("purpose", "any").split())
                static_path = icon["src"].removeprefix("/static/")
                self.assertIsNotNone(finders.find(static_path), static_path)

    def test_pwa_bootstrap_responses_are_not_cached(self):
        for url_name in ("manifest.json", "service-worker.js"):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertIn("no-cache", response.headers["Cache-Control"])

    def test_service_worker_precaches_manifest_png_icons(self):
        manifest = json.loads(self.client.get(reverse("manifest.json")).content)
        worker_source = self.client.get(reverse("service-worker.js")).content.decode()

        for icon in manifest["icons"]:
            if icon["type"] == "image/png":
                with self.subTest(icon=icon["src"]):
                    self.assertRegex(worker_source, rf"['\"]{re.escape(icon['src'])}['\"]")
