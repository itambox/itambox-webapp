import django_tables2 as tables
from django.utils.html import format_html
from core.models import ObjectChange
from .base import BaseTable

class ObjectChangeTable(BaseTable):
    time = tables.DateTimeColumn(linkify=True, format='Y-m-d H:i:s')
    user_name = tables.Column(verbose_name='User')
    action = tables.Column(verbose_name='Action')
    changed_object_type = tables.Column(linkify=False, verbose_name='Type')
    object_repr = tables.Column(linkify=False, verbose_name='Object')
    request_id = tables.Column(linkify=False, verbose_name='Request ID')

    changed_object = tables.Column(
        linkify=lambda record: record.get_changed_object_url(),
        verbose_name='Changed Object',
        accessor='object_repr'
    )

    class Meta(BaseTable.Meta):
        model = ObjectChange
        fields = (
            'time', 'user_name', 'action', 'changed_object_type', 'changed_object',
            'request_id',
        )

    def render_action(self, value, record):
        from core.choices import ObjectChangeActionChoices
        color = 'secondary'
        for val, label, c in ObjectChangeActionChoices.CHOICES:
            if val == value:
                color = c
                break
        return format_html(
            '<span class="badge bg-{0}">{1}</span>',
            color,
            record.get_action_display(),
        )
