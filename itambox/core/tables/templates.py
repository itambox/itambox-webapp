import django_tables2 as tables
from django.contrib.contenttypes.models import ContentType
from core.models import ExportTemplate, WebhookEndpoint, EventRule, LabelTemplate
from .base import BaseTable
from .columns import BooleanColumn

class ExportTemplateTable(BaseTable):
    name = tables.Column(linkify=True)
    content_type = tables.Column(verbose_name='Model')
    file_extension = tables.Column(verbose_name='File Type')
    mime_type = tables.Column()

    class Meta(BaseTable.Meta):
        model = ExportTemplate
        fields = ('name', 'content_type', 'file_extension', 'mime_type')
        sequence = ('name', 'content_type', 'file_extension', 'mime_type')

    def render_content_type(self, value):
        return f"{value.app_label}.{value.model}"


class SearchResultTable(tables.Table):
    object_type = tables.Column(
        accessor='_object_type_id',
        verbose_name='Type',
        orderable=False
    )
    object = tables.Column(
        accessor='object',
        linkify=True,
        verbose_name='Result',
        orderable=False
    )

    class Meta:
        attrs = {
            'class': 'table table-hover table-vcenter card-table'
        }
        fields = ('object_type', 'object',)

    def render_object_type(self, value):
        try:
            ct = ContentType.objects.get_for_id(value)
            return ct.name.capitalize()
        except ContentType.DoesNotExist:
            return "Unknown Type"


class WebhookEndpointTable(BaseTable):
    name = tables.Column(linkify=True)
    url = tables.Column()
    http_method = tables.Column(verbose_name='Method')
    enabled = BooleanColumn()
    retry_count = tables.Column(verbose_name='Retries')

    class Meta(BaseTable.Meta):
        model = WebhookEndpoint
        fields = ('name', 'url', 'http_method', 'enabled', 'retry_count')
        sequence = ('name', 'url', 'http_method', 'enabled', 'retry_count')


class EventRuleTable(BaseTable):
    name = tables.Column(linkify=True)
    model = tables.Column(verbose_name='Model')
    action_type = tables.Column(verbose_name='Action')
    enabled = BooleanColumn()

    class Meta(BaseTable.Meta):
        model = EventRule
        fields = ('name', 'model', 'action_type', 'enabled')
        sequence = ('name', 'model', 'action_type', 'enabled')

    def render_model(self, value):
        return f"{value.app_label}.{value.model}"

    def render_action_type(self, value):
        from core.models import EventRule as ER
        action_map = dict(ER.ACTION_TYPE_CHOICES)
        return action_map.get(value, value)


class LabelTemplateTable(BaseTable):
    name = tables.Column(linkify=True)
    description = tables.Column()
    page_width = tables.Column(verbose_name='Width (in)')
    page_height = tables.Column(verbose_name='Height (in)')
    barcode_format = tables.Column(verbose_name='Barcode')

    class Meta(BaseTable.Meta):
        model = LabelTemplate
        fields = ('name', 'description', 'page_width', 'page_height', 'barcode_format')
        sequence = ('name', 'description', 'page_width', 'page_height', 'barcode_format')

    def render_barcode_format(self, value):
        from core.models import LabelTemplate as LT
        fmt_map = dict(LT._meta.get_field('barcode_format').choices)
        return fmt_map.get(value, value)
