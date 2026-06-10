import django_tables2 as tables
from django.utils.html import format_html
from extras.models import ReportTemplate, ScheduledReport
from .base import BaseTable
from .columns import BooleanColumn

class ReportTemplateTable(BaseTable):
    name = tables.Column(linkify=True)
    report_type = tables.Column(verbose_name='Type')

    class Meta(BaseTable.Meta):
        model = ReportTemplate
        fields = ('name', 'description', 'report_type')
        sequence = ('name', 'description', 'report_type')


class ScheduledReportTable(BaseTable):
    name = tables.Column(linkify=False)
    report = tables.Column(linkify=True)
    recipients = tables.Column()
    format = tables.Column()
    is_active = BooleanColumn()
    last_run = tables.DateTimeColumn(format='Y-m-d H:i:s')
    last_status = tables.Column()
    actions = tables.TemplateColumn(
        template_code="""
        <div class="d-flex gap-1 justify-content-end">
            <form method="post" action="{% url 'scheduledreport_trigger' record.pk %}" class="d-inline">
                {% csrf_token %}
                <input type="hidden" name="return_url" value="{{ request.get_full_path }}">
                <button type="submit" class="btn btn-sm btn-outline-primary d-flex align-items-center" title="Run Now">
                    <i class="mdi mdi-play"></i>
                    <span class="ms-1 d-none d-md-inline">Run Now</span>
                </button>
            </form>
            <a class="btn btn-sm btn-outline-secondary btn-icon" href="{% url 'scheduledreport_edit' record.pk %}" title="Edit">
                <i class="mdi mdi-pencil-outline"></i>
            </a>
            <a class="btn btn-sm btn-outline-danger btn-icon" href="{% url 'scheduledreport_delete' record.pk %}" title="Delete">
                <i class="mdi mdi-trash-can-outline"></i>
            </a>
        </div>
        """,
        verbose_name="Actions",
        orderable=False,
        attrs={
            'th': {
                'class': 'col-actions-wide text-nowrap',
            },
            'td': {
                'class': 'text-end text-nowrap noprint p-1 col-actions-wide'
            }
        }
    )

    class Meta(BaseTable.Meta):
        model = ScheduledReport
        fields = ('name', 'report', 'recipients', 'format', 'is_active', 'last_run', 'last_status', 'actions')
        sequence = ('name', 'report', 'recipients', 'format', 'is_active', 'last_run', 'last_status', 'actions')
