import django_tables2 as tables
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from core.models import Job
from .base import BaseTable

class JobTable(BaseTable):
    name = tables.Column(linkify=True)
    status = tables.Column(verbose_name=_('Status'))
    created = tables.DateTimeColumn(verbose_name=_('Created At'), format='Y-m-d H:i:s')
    started = tables.DateTimeColumn(verbose_name=_('Started At'), format='Y-m-d H:i:s')
    completed = tables.DateTimeColumn(verbose_name=_('Completed At'), format='Y-m-d H:i:s')

    class Meta(BaseTable.Meta):
        model = Job
        fields = ('name', 'status', 'created', 'started', 'completed')
        sequence = ('name', 'status', 'created', 'started', 'completed')

    def render_status(self, value, record):
        color = 'secondary'
        if value == 'pending':
            color = 'info'
        elif value == 'running':
            color = 'warning'
        elif value == 'completed':
            color = 'success'
        elif value == 'failed':
            color = 'danger'
        return format_html(
            '<span class="badge bg-{0}">{1}</span>',
            color,
            record.get_status_display(),
        )
