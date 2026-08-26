import django_tables2 as tables
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _


class SearchResultTable(tables.Table):
    object_type = tables.Column(accessor="_object_type_id", verbose_name=_("Type"), orderable=False)
    object = tables.Column(accessor="object", linkify=True, verbose_name=_("Result"), orderable=False)

    class Meta:
        attrs = {"class": "table table-hover table-vcenter card-table"}
        fields = (
            "object_type",
            "object",
        )

    def render_object_type(self, value):
        try:
            ct = ContentType.objects.get_for_id(value)
            return ct.name.capitalize()
        except ContentType.DoesNotExist:
            return _("Unknown Type")
