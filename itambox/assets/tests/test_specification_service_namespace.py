from pathlib import Path

from django.test import SimpleTestCase

import assets.services as services
from assets.services import checkin_asset, checkout_asset, checkout_kit, dispose_asset


class SpecificationServiceNamespaceTests(SimpleTestCase):
    def test_existing_service_symbols_resolve_from_package_initializer(self):
        service_path = Path(services.__file__)

        self.assertEqual(service_path.name, "__init__.py")
        self.assertEqual(service_path.parent.name, "services")
        for service in (checkin_asset, checkout_asset, checkout_kit, dispose_asset):
            self.assertEqual(service.__module__, "assets.services")

        self.assertEqual(services.logger.name, "assets.services")
        self.assertTrue(callable(services.send_mail))
