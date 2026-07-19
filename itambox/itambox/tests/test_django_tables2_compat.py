from pathlib import Path
import unittest

from django.conf import settings
from django.template import Engine


if not settings.configured:
    settings.configure(
        DJANGO_TABLES2_TEMPLATE="django_tables2/bootstrap5.html",
    )


class DjangoTables2TemplateCompatibilityTests(unittest.TestCase):
    def test_htmx_sort_link_compiles_with_supported_django_tables2(self):
        template_path = (
            Path(__file__).resolve().parents[2]
            / "templates"
            / "global_includes"
            / "htmx_table.html"
        )
        sort_link = next(
            line
            for line in template_path.read_text(encoding="utf-8").splitlines()
            if "table.prefixed_order_by_field=" in line
        )
        engine = Engine(
            libraries={
                "django_tables2": "django_tables2.templatetags.django_tables2",
            }
        )

        engine.from_string("{% load django_tables2 %}\n" + sort_link)


if __name__ == "__main__":
    unittest.main()
