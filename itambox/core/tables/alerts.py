import django_tables2 as tables
from django.utils.html import format_html
from core.models import AlertLog, AlertRule, NotificationChannel
from .base import BaseTable
from .columns import BooleanColumn

class AlertRuleTable(BaseTable):
    name = tables.Column(linkify=True)
    alert_type = tables.Column(verbose_name='Alert Type')
    threshold_value = tables.Column(verbose_name='Threshold')
    severity = tables.Column()
    is_active = BooleanColumn()
    tenant = tables.Column(verbose_name='Tenant', accessor='tenant.name')

    class Meta(BaseTable.Meta):
        model = AlertRule
        fields = ('name', 'alert_type', 'threshold_value', 'severity', 'is_active', 'tenant')
        sequence = ('name', 'alert_type', 'threshold_value', 'severity', 'is_active', 'tenant')

    def render_severity(self, value):
        color = 'secondary'
        if value == AlertRule.SEVERITY_INFO:
            color = 'info'
        elif value == AlertRule.SEVERITY_WARNING:
            color = 'warning'
        elif value == AlertRule.SEVERITY_CRITICAL:
            color = 'danger'
        return format_html('<span class="badge bg-{}">{}</span>', color, value.capitalize())


class NotificationChannelTable(BaseTable):
    name = tables.Column(linkify=True)
    channel_type = tables.Column(verbose_name='Channel Type')
    enabled = BooleanColumn()
    tenant = tables.Column(verbose_name='Tenant', accessor='tenant.name')
    actions = tables.TemplateColumn(
        template_code="""
        <div class="d-flex gap-1 justify-content-end">
            <form method="post" action="{% url 'notificationchannel_test' record.pk %}" class="d-inline">
                {% csrf_token %}
                <button type="submit" class="btn btn-sm btn-outline-info btn-icon" title="Send test notification">
                    <i class="mdi mdi-send-outline"></i>
                </button>
            </form>
            <a class="btn btn-sm btn-outline-secondary btn-icon" href="{% url 'notificationchannel_edit' record.pk %}" title="Edit">
                <i class="mdi mdi-pencil-outline"></i>
            </a>
            <a class="btn btn-sm btn-outline-danger btn-icon" href="{% url 'notificationchannel_delete' record.pk %}" title="Delete">
                <i class="mdi mdi-trash-can-outline"></i>
            </a>
        </div>
        """,
        verbose_name="Actions",
        orderable=False,
        attrs={
            'th': {
                'class': 'col-actions text-nowrap',
            },
            'td': {
                'class': 'text-end text-nowrap noprint p-1 col-actions'
            }
        }
    )

    class Meta(BaseTable.Meta):
        model = NotificationChannel
        fields = ('name', 'channel_type', 'enabled', 'tenant', 'actions')
        sequence = ('name', 'channel_type', 'enabled', 'tenant', 'actions')


class AlertLogTable(BaseTable):
    created_at = tables.DateTimeColumn(verbose_name='Date', format='Y-m-d H:i:s')
    rule = tables.Column(linkify=True)
    subject = tables.Column(linkify=False)
    severity = tables.Column()
    status = tables.Column()
    actions = tables.TemplateColumn(
        template_code="""
        <div class="d-flex gap-1 justify-content-end">
            {% if record.status == 'active' %}
                <form method="post" action="{% url 'alertlog_acknowledge' record.pk %}" class="d-inline">
                    {% csrf_token %}
                    <input type="hidden" name="return_url" value="{{ request.get_full_path }}">
                    <button type="submit" class="btn btn-sm btn-outline-warning" title="Acknowledge">
                        <i class="mdi mdi-eye-outline"></i>
                        Acknowledge
                    </button>
                </form>
            {% endif %}
            {% if record.status != 'resolved' %}
                <form method="post" action="{% url 'alertlog_resolve' record.pk %}" class="d-inline">
                    {% csrf_token %}
                    <input type="hidden" name="return_url" value="{{ request.get_full_path }}">
                    <button type="submit" class="btn btn-sm btn-outline-success" title="Resolve">
                        <i class="mdi mdi-check"></i>
                        Resolve
                    </button>
                </form>
            {% endif %}
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
        model = AlertLog
        fields = ('created_at', 'rule', 'subject', 'severity', 'status', 'actions')
        sequence = ('created_at', 'rule', 'subject', 'severity', 'status', 'actions')

    def render_severity(self, value):
        color = 'secondary'
        if value == AlertRule.SEVERITY_INFO:
            color = 'info'
        elif value == AlertRule.SEVERITY_WARNING:
            color = 'warning'
        elif value == AlertRule.SEVERITY_CRITICAL:
            color = 'danger'
        return format_html('<span class="badge bg-{}">{}</span>', color, value.capitalize())

    def render_status(self, value):
        color = 'secondary'
        if value == AlertLog.STATUS_ACTIVE:
            color = 'danger'
        elif value == AlertLog.STATUS_ACKNOWLEDGED:
            color = 'warning'
        elif value == AlertLog.STATUS_RESOLVED:
            color = 'success'
        return format_html('<span class="badge bg-{}">{}</span>', color, value.capitalize())
