# itambox/extras/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from django.utils.safestring import mark_safe
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.translation import gettext_lazy as _
from .models import (
    Tag, CustomField, CustomFieldset, SavedFilter,
    AlertRule, AlertLog, NotificationChannel, ReportTemplate, ScheduledReport,
)
from core.tables import ActionsColumn, BaseTable, ToggleColumn, BooleanColumn

# =============================================================================
# Custom Columns
# =============================================================================

class TagColumn(tables.ManyToManyColumn):
    """
    A table column which renders linked tags for an object.
    """
    def __init__(self, url_name=None, *args, **kwargs):
        self.url_name = url_name
        # Prevent default linking of ManyToManyColumn
        kwargs.setdefault('linkify_item', False) 
        super().__init__(*args, **kwargs)

    def render(self, value):
        if not value:
            return self.default or ""
        tags = list(self.filter(value))
        if not tags:
            return self.default or ""

        limit = 3
        visible_tags = tags[:limit]
        remaining_count = len(tags) - limit

        rendered_tags = []
        for tag in visible_tags:
            color_hex = tag.color or "6c757d"  # fallback default color if empty
            
            # calculate contrast color using YIQ formula
            try:
                r = int(color_hex[0:2], 16)
                g = int(color_hex[2:4], 16)
                b = int(color_hex[4:6], 16)
                yiq = ((r * 299) + (g * 587) + (b * 114)) / 1000
                text_color = "#212529" if yiq >= 150 else "#ffffff"
            except Exception:
                text_color = "#ffffff"

            url = reverse(self.url_name or 'extras:tag_list') + '?tag=' + escape(tag.slug)
            rendered_tags.append(format_html(
                '<a href="{}" class="badge me-1" style="background-color: #{}; color: {};">{}</a>',
                url, color_hex, text_color, tag.name
            ))

        if remaining_count > 0:
            rendered_tags.append(format_html(
                '<span class="badge bg-secondary" title="{} tags total">+{}</span>',
                len(tags), remaining_count
            ))

        return mark_safe("".join(rendered_tags))

# =============================================================================
# Model Tables
# =============================================================================

class TagTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('extras:tag_detail', args=[A('pk')], verbose_name=_('Name'))
    # You might want a column to show count of items tagged with this tag.
    # This can be complex depending on how Tags are related (GenericForeignKey?).
    # For now, let's omit the count.
    # item_count = tables.Column(verbose_name='Tagged Items', orderable=False, empty_values=())
    color = tables.Column(verbose_name=_('Color'), orderable=True)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Tag
        fields = ('pk', 'name', 'slug', 'color', 'description', 'actions')
        default_columns = ('pk', 'name', 'color', 'description', 'actions')

    def render_color(self, value):
        if value:
            return format_html('<span class="badge" style="background-color: #{};">&nbsp;</span> #{}', value, value)
        return "—"


class CustomFieldTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('extras:customfield_detail', args=[A('pk')], verbose_name=_('Name'))
    label = tables.Column(verbose_name=_('Label'))
    field_type = tables.Column(verbose_name=_('Field Type'))
    required = tables.BooleanColumn(verbose_name=_('Required'))
    object_types = tables.ManyToManyColumn(
        verbose_name=_('Applies To'),
        transform=lambda ct: ct.model_class()._meta.verbose_name.title() if ct.model_class() else ct.model,
        orderable=False,
    )
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = CustomField
        fields = ('pk', 'name', 'label', 'field_type', 'required', 'object_types', 'actions')
        default_columns = ('pk', 'name', 'label', 'field_type', 'required', 'object_types', 'actions')


class CustomFieldsetTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('extras:customfieldset_detail', args=[A('pk')], verbose_name=_('Name'))
    fields_count = tables.Column(verbose_name=_('Fields Count'), orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = CustomFieldset
        fields = ('pk', 'name', 'fields_count', 'actions')
        default_columns = ('pk', 'name', 'fields_count', 'actions')

    def render_fields_count(self, value, record=None):
        return value or 0


class SavedFilterTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('extras:savedfilter_detail', args=[A('pk')], verbose_name=_('Name'))
    content_type = tables.Column(verbose_name=_('Object Type'), accessor='content_type')
    shared = BooleanColumn(verbose_name=_('Shared'))
    enabled = BooleanColumn(verbose_name=_('Enabled'))
    tenant = tables.Column(verbose_name=_('Tenant'), accessor='tenant.name', linkify=False)
    created_by = tables.Column(verbose_name=_('Created By'), accessor='created_by')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = SavedFilter
        fields = ('pk', 'name', 'content_type', 'shared', 'enabled', 'tenant', 'created_by', 'actions')
        default_columns = ('pk', 'name', 'content_type', 'shared', 'enabled', 'tenant', 'created_by', 'actions')

    def render_content_type(self, value):
        model = value.model_class()
        return model._meta.verbose_name.title() if model else value.model

    def render_tenant(self, value):
        return value or mark_safe('<span class="badge bg-secondary">Global</span>')


# =============================================================================
# Alerting Tables
# =============================================================================

class AlertRuleTable(BaseTable):
    name = tables.Column(linkify=True)
    alert_type = tables.Column(verbose_name=_('Alert Type'))
    threshold_value = tables.Column(verbose_name=_('Threshold'))
    severity = tables.Column()
    is_active = BooleanColumn()
    is_muted = BooleanColumn(verbose_name=_('Muted'))
    tenant = tables.Column(verbose_name=_('Tenant'), accessor='tenant.name', linkify=False)
    actions = tables.TemplateColumn(
        template_code="""
        <div class="d-flex gap-1 justify-content-end">
            <form method="post" action="{% url 'extras:alertrule_run' record.pk %}" class="d-inline">
                {% csrf_token %}
                <input type="hidden" name="return_url" value="{{ request.get_full_path }}">
                <button type="submit" class="btn btn-sm btn-outline-primary btn-icon" title="Run now">
                    <i class="mdi mdi-play-circle-outline"></i>
                </button>
            </form>
            <a class="btn btn-sm btn-outline-secondary btn-icon" href="{% url 'extras:alertrule_update' record.pk %}" title="Edit">
                <i class="mdi mdi-pencil-outline"></i>
            </a>
        </div>
        """,
        verbose_name=_("Actions"),
        orderable=False,
        attrs={
            'th': {'class': 'col-actions text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions'},
        }
    )

    class Meta(BaseTable.Meta):
        model = AlertRule
        fields = ('name', 'alert_type', 'threshold_value', 'severity', 'is_active', 'is_muted', 'tenant', 'actions')
        sequence = ('name', 'alert_type', 'threshold_value', 'severity', 'is_active', 'is_muted', 'tenant', 'actions')

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
    name = tables.Column(linkify=False)
    channel_type = tables.Column(verbose_name=_('Channel Type'))
    enabled = BooleanColumn()
    tenant = tables.Column(verbose_name=_('Tenant'), accessor='tenant.name', linkify=False)
    actions = tables.TemplateColumn(
        template_code="""
        <div class="d-flex gap-1 justify-content-end">
            <form method="post" action="{% url 'extras:notificationchannel_test' record.pk %}" class="d-inline">
                {% csrf_token %}
                <button type="submit" class="btn btn-sm btn-outline-info btn-icon" title="Send test notification">
                    <i class="mdi mdi-send-outline"></i>
                </button>
            </form>
            <a class="btn btn-sm btn-outline-secondary btn-icon" href="{% url 'extras:notificationchannel_update' record.pk %}" title="Edit">
                <i class="mdi mdi-pencil-outline"></i>
            </a>
            <a class="btn btn-sm btn-outline-danger btn-icon" href="{% url 'extras:notificationchannel_delete' record.pk %}" title="Delete">
                <i class="mdi mdi-trash-can-outline"></i>
            </a>
        </div>
        """,
        verbose_name=_("Actions"),
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
    pk = ToggleColumn(accessor='pk')
    created_at = tables.DateTimeColumn(verbose_name=_('Date'), format='Y-m-d H:i:s')
    rule = tables.Column(linkify=True)
    subject = tables.Column(linkify=False)
    severity = tables.Column()
    status = tables.Column()
    delivery = tables.Column(
        verbose_name=_('Delivery'), orderable=False, empty_values=(), accessor='delivery_status'
    )
    actions = tables.TemplateColumn(
        template_code="""
        <div class="d-flex gap-1 justify-content-end">
            {% if record.status == 'active' %}
                <form method="post" action="{% url 'extras:alertlog_acknowledge' record.pk %}" class="d-inline">
                    {% csrf_token %}
                    <input type="hidden" name="return_url" value="{{ request.get_full_path }}">
                    <button type="submit" class="btn btn-sm btn-outline-warning" title="Acknowledge">
                        <i class="mdi mdi-eye-outline"></i>
                        Acknowledge
                    </button>
                </form>
            {% endif %}
            {% if record.status != 'resolved' %}
                <form method="post" action="{% url 'extras:alertlog_resolve' record.pk %}" class="d-inline">
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
        verbose_name=_("Actions"),
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
        fields = ('pk', 'created_at', 'rule', 'subject', 'severity', 'status', 'delivery', 'actions')
        sequence = ('pk', 'created_at', 'rule', 'subject', 'severity', 'status', 'delivery', 'actions')
        empty_text = _('All clear. No alerts match the current filters.')

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

    def render_delivery(self, record):
        statuses = record.delivery_status or {}
        if not statuses:
            return format_html('<span class="text-muted" title="No channels / not yet dispatched">&mdash;</span>')
        total = len(statuses)
        failed = [k for k, v in statuses.items() if v != 'ok']
        if not failed:
            return format_html(
                '<span class="badge bg-success" title="All {} channel(s) delivered">{}/{}</span>',
                total, total, total,
            )
        failed_detail = '; '.join(f"{k}: {statuses[k]}" for k in failed)
        return format_html(
            '<span class="badge bg-danger" title="{}">{}/{} failed</span>',
            failed_detail, len(failed), total,
        )


# =============================================================================
# Reporting Tables
# =============================================================================

class ReportTemplateTable(BaseTable):
    name = tables.Column(linkify=True)
    report_type = tables.Column(verbose_name=_('Type'))

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
            <form method="post" action="{% url 'extras:scheduledreport_trigger' record.pk %}" class="d-inline">
                {% csrf_token %}
                <input type="hidden" name="return_url" value="{{ request.get_full_path }}">
                <button type="submit" class="btn btn-sm btn-outline-primary d-flex align-items-center" title="Run Now">
                    <i class="mdi mdi-play"></i>
                    <span class="ms-1 d-none d-md-inline">Run Now</span>
                </button>
            </form>
            <a class="btn btn-sm btn-outline-secondary btn-icon" href="{% url 'extras:scheduledreport_update' record.pk %}" title="Edit">
                <i class="mdi mdi-pencil-outline"></i>
            </a>
            <a class="btn btn-sm btn-outline-danger btn-icon" href="{% url 'extras:scheduledreport_delete' record.pk %}" title="Delete">
                <i class="mdi mdi-trash-can-outline"></i>
            </a>
        </div>
        """,
        verbose_name=_("Actions"),
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


