import logging
import django_tables2 as tables
from django_tables2.utils import A
from django_tables2.data import TableQuerysetData
from .models import (
    ObjectChange, ExportTemplate, PermissionGroup, WebhookEndpoint,
    EventRule, LabelTemplate, Job, ReportTemplate, ScheduledReport,
    AlertRule, AlertLog, NotificationChannel
)
from django.utils.html import format_html, escape
from django.urls import reverse, NoReverseMatch
from django.utils.safestring import mark_safe
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from .utils import get_model_viewname
from django.contrib.contenttypes.models import ContentType
from django.conf import settings

from core.paginator import EnhancedPaginator, get_paginate_count

logger = logging.getLogger(__name__)

# =============================================================================
# Custom Columns
# =============================================================================

class BooleanColumn(tables.Column):
    TRUE_MARK = mark_safe(
        '<span class="text-success">'
        '<i class="mdi mdi-check-circle-outline"></i>'
        '</span>'
    )
    FALSE_MARK = mark_safe(
        '<span class="text-danger">'
        '<i class="mdi mdi-close-circle-outline"></i>'
        '</span>'
    )
    EMPTY_MARK = mark_safe('<span class="text-muted">&mdash;</span>')

    def __init__(self, *args, true_mark=None, false_mark=None, **kwargs):
        self.true_mark = true_mark if true_mark is not None else self.TRUE_MARK
        self.false_mark = false_mark if false_mark is not None else self.FALSE_MARK
        super().__init__(*args, **kwargs)

    def render(self, value):
        if value is True:
            return self.true_mark
        elif value is False:
            return self.false_mark
        return self.EMPTY_MARK


class ToggleColumn(tables.CheckBoxColumn):
    def __init__(self, *args, **kwargs):
        default = kwargs.pop('default', '')
        visible = kwargs.pop('visible', True)
        if 'attrs' not in kwargs:
            kwargs['attrs'] = {
                'th': {
                    'class': 'w-1 text-nowrap',
                    'aria-label': _('Select all'),
                },
                'td': {
                    'class': 'w-1 text-nowrap',
                },
                'input': {
                    'class': 'form-check-input',
                }
            }
        super().__init__(*args, default=default, visible=visible, **kwargs)

    @property
    def header(self):
        title_text = _('Toggle all')
        return format_html(
            '<input type="checkbox" class="toggle form-check-input" name="select_all" title="{}" aria-label="{}" />',
            title_text, title_text,
        )


class ActionsColumn(tables.Column):
    attrs = {
        'th': {
            'class': 'col-actions text-nowrap',
        },
        'td': {
            'class': 'text-end text-nowrap noprint p-1 col-actions'
        }
    }
    empty_values = ()
    verbose_name = ''
    orderable = False

    actions = {
        'edit': {'title': 'Edit', 'icon': 'pencil', 'css_class': 'primary'},
        'delete': {'title': 'Delete', 'icon': 'trash', 'css_class': 'danger'},
    }

    def __init__(self, *args, actions=('edit', 'delete'), extra_buttons='', split_actions=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra_buttons = extra_buttons
        self.split_actions = split_actions
        self.actions = {
            name: self.actions[name] for name in actions if name in self.actions
        }

    def render(self, record, table, **kwargs):
        if not self.actions and not self.extra_buttons:
            return ''
        if not getattr(record, 'pk', None):
            return ''

        model = type(record)

        icon_edit = '<i class="mdi mdi-pencil-outline"></i>'
        icon_delete = '<i class="mdi mdi-trash-can-outline"></i>'

        icons = {
            'edit': icon_edit,
            'delete': icon_delete,
        }

        html = ''
        button = None
        dropdown_class = 'secondary'
        dropdown_links = []

        for idx, (action, attrs) in enumerate(self.actions.items()):
            css_class = attrs['css_class']
            icon = icons.get(action, '')
            viewname = get_model_viewname(model, 'update' if action == 'edit' else 'delete')
            url = None
            for kwargs in ({'pk': record.pk}, {'slug': getattr(record, 'slug', None)}):
                if None in kwargs.values():
                    continue
                try:
                    url = reverse(viewname, kwargs=kwargs)
                    break
                except NoReverseMatch:
                    continue
            if url is None:
                continue

            if len(self.actions) == 1 or (self.split_actions and idx == 0):
                dropdown_class = css_class
                button = (
                    f'<a class="btn btn-sm btn-{css_class}" href="{url}" type="button" '
                    f'aria-label="{attrs["title"]}">{icon}</a>'
                )
            else:
                dropdown_links.append(
                    f'<li><a class="dropdown-item" href="{url}">{icon} {attrs["title"]}</a></li>'
                )

        toggle_text = _('Toggle Dropdown')
        if button and dropdown_links:
            html += (
                f'<span class="btn-group dropdown">'
                f'  {button}'
                f'  <a class="btn btn-sm btn-{dropdown_class} dropdown-toggle" type="button" data-bs-toggle="dropdown" '
                f'style="padding-left: 2px">'
                f'  <span class="visually-hidden">{toggle_text}</span></a>'
                f'  <ul class="dropdown-menu">{"".join(dropdown_links)}</ul>'
                f'</span>'
            )
        elif button:
            html += button
        elif dropdown_links:
            html += (
                f'<span class="btn-group dropdown">'
                f'  <a class="btn btn-sm btn-secondary dropdown-toggle" type="button" data-bs-toggle="dropdown">'
                f'  <span class="visually-hidden">{toggle_text}</span></a>'
                f'  <ul class="dropdown-menu">{"".join(dropdown_links)}</ul>'
                f'</span>'
            )

        return mark_safe(html)


class AssigneeColumn(tables.Column):
    """
    A reusable column that resolves an AssetHolder (via AssetAssignment
    ForeignKey) for each row, falling back to a named model field
    (e.g., ``location``) when no holder is assigned.

    The bulk assignment lookup is cached on the parent table instance so
    that only a single database query is issued per table rendering.

    Usage::

        assignee = AssigneeColumn(location_field='location')

    Parameters:
        location_field (str | None): Model field name to display as fallback
            when no AssetHolder is assigned.
        empty_text (str): Displayed when no holder or location is available.
        assignment_model_path (str): Dotted app-model path to the
            AssetAssignment-like model.
    """

    EMPTY_MARK = mark_safe('<span class="text-muted">&mdash;</span>')

    def __init__(
        self,
        *args,
        location_field=None,
        empty_text=None,
        assignment_model_path='assets.AssetAssignment',
        **kwargs,
    ):
        kwargs.setdefault('verbose_name', 'Assignee')
        kwargs.setdefault('orderable', False)
        kwargs.setdefault('accessor', 'pk')
        self.location_field = location_field
        self._empty_text = empty_text
        self._assignment_model_path = assignment_model_path
        super().__init__(*args, **kwargs)

    def _get_assignment_model(self):
        from django.apps import apps
        return apps.get_model(self._assignment_model_path)

    def get_prefetch_fields(self):
        return [
            'assignments',
            'assignments__assigned_user',
            'assignments__assigned_location',
            'assignments__assigned_asset',
        ]

    def render(self, value, record, bound_column, table=None):
        if table is None:
            table = bound_column._table
        cache_attr = f'_assignee_cache_{id(self)}'
        if not hasattr(table, cache_attr):
            self._build_cache(table, record.__class__, cache_attr)
        cache = getattr(table, cache_attr)

        holder = cache.get(value)
        if holder:
            try:
                url = holder.get_absolute_url()
                from organization.models import Location
                if isinstance(holder, Location):
                    return format_html('Location: <a href="{}">{}</a>', url, holder)
                return format_html('<a href="{}">{}</a>', url, holder)
            except Exception:
                return str(holder)

        if hasattr(record, 'active_assignment') and record.active_assignment is None:
            return self.EMPTY_MARK

        if self.location_field and hasattr(record, self.location_field):
            loc = getattr(record, self.location_field)
            if loc:
                try:
                    url = loc.get_absolute_url()
                    return format_html('Location: <a href="{}">{}</a>', url, loc)
                except Exception:
                    return f"Location: {loc}"

        if self._empty_text is not None:
            return self._empty_text
        return self.EMPTY_MARK

    def _build_cache(self, table, model_class, cache_attr):
        from django.contrib.contenttypes.models import ContentType
        AssignmentModel = self._get_assignment_model()
        pks = [row.pk for row in table.data]
        if not pks:
            setattr(table, cache_attr, {})
            return

        cache = {}
        
        # Check if AssignmentModel has a direct ForeignKey to model_class
        fk_field = None
        for field in AssignmentModel._meta.fields:
            if field.is_relation and field.related_model == model_class:
                fk_field = field
                break

        if fk_field is not None:
            filter_kwargs = {f"{fk_field.name}_id__in": pks}
            if hasattr(AssignmentModel, 'is_active'):
                filter_kwargs['is_active'] = True

            select_rels = []
            for f in AssignmentModel._meta.fields:
                if f.name in ('assigned_user', 'assigned_location', 'assigned_asset', 'assigned_holder'):
                    select_rels.append(f.name)
            assignments = AssignmentModel.objects.filter(**filter_kwargs)
            if select_rels:
                assignments = assignments.select_related(*select_rels)
            elif hasattr(AssignmentModel, 'assigned_to_content_type'):
                assignments = assignments.select_related('assigned_to_content_type')

            if hasattr(AssignmentModel, 'assigned_to_content_type') and not select_rels:
                # Handle old GenericForeignKey case
                assigned_to_ids_by_ct = {}
                for a in assignments:
                    ct_id = getattr(a, 'assigned_to_content_type_id', None)
                    if not ct_id:
                        continue
                    if ct_id not in assigned_to_ids_by_ct:
                        assigned_to_ids_by_ct[ct_id] = []
                    parent_id = getattr(a, f"{fk_field.name}_id")
                    assigned_to_ids_by_ct[ct_id].append((parent_id, getattr(a, 'assigned_to_object_id')))

                ct_map = {}
                for ct_id in assigned_to_ids_by_ct:
                    ct = ContentType.objects.get_for_id(ct_id)
                    ct_map[ct_id] = ct

                for ct_id, entries in assigned_to_ids_by_ct.items():
                    ct = ct_map.get(ct_id)
                    if ct is None:
                        continue
                    model = ct.model_class()
                    if model is None:
                        continue
                    obj_ids = [e[1] for e in entries]
                    instances = model.objects.filter(pk__in=obj_ids)
                    instance_map = {obj.pk: obj for obj in instances}
                    for parent_id, obj_id in entries:
                        cache[parent_id] = instance_map.get(obj_id)
            else:
                # Handle explicit FKs case
                for a in assignments:
                    parent_id = getattr(a, f"{fk_field.name}_id")
                    target = getattr(a, 'assigned_target', None)
                    if target is None:
                        target = getattr(a, 'assigned_holder', None) or getattr(a, 'assigned_user', None) or getattr(a, 'assigned_location', None) or getattr(a, 'assigned_asset', None)
                    cache[parent_id] = target

        else:
            ct = ContentType.objects.get_for_model(model_class)
            assignments = AssignmentModel.objects.filter(
                content_type=ct, object_id__in=pks
            ).select_related('asset_holder')
            cache = {
                a.object_id: a.asset_holder
                for a in assignments
                if getattr(a, 'asset_holder', None)
            }

        setattr(table, cache_attr, cache)


# Base Table for common settings
class BaseTable(tables.Table):
    exempt_columns = ('pk', 'actions')

    class Meta:
        model = None
        attrs = {
            'class': 'table table-hover table-vcenter card-table',
            'thead': {
                'class': 'text-nowrap'
            },
            'td': {
                'class': 'text-nowrap'
            }
        }
        exclude_from_config = ('pk', 'actions')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.empty_text is None:
            model = getattr(self.Meta, 'model', None)
            if model:
                self.empty_text = _("No {model_name} found").format(
                    model_name=model._meta.verbose_name_plural
                )
            else:
                self.empty_text = _('No results found')

        self._apply_column_width_classes()

    def _apply_column_width_classes(self):
        width_heuristics = {
            'name': 'col-name',
            'description': 'col-name',
            'serial_number': 'col-serial',
            'asset_tag': 'col-tag',
            'part_number': 'col-tag',
            'tenant': 'col-relation',
            'location': 'col-relation',
            'supplier': 'col-relation',
            'manufacturer': 'col-relation',
            'assignee': 'col-relation',
            'contacts': 'col-relation',
            'actions': 'col-actions',
        }
        for col_name, width_class in width_heuristics.items():
            if col_name not in self.columns:
                continue
            base_column = self.columns[col_name].column
            if base_column.attrs is None:
                base_column.attrs = {}
            for attr_key in ('th', 'td'):
                cell_attrs = base_column.attrs.setdefault(attr_key, {})
                current_class = cell_attrs.get('class', '')
                if 'col-actions-wide' in current_class and width_class == 'col-actions':
                    continue
                cell_attrs['class'] = f"{current_class} {width_class}".strip()

    def _get_columns(self, visible=True):
        columns = []
        for name, column in self.columns.items():
            if column.visible == visible and name not in self.exempt_columns:
                columns.append((name, column.verbose_name))
        return columns

    @property
    def name(self):
        return self.__class__.__name__

    @property
    def available_columns(self):
        return sorted(self._get_columns(visible=False))

    @property
    def selected_columns(self):
        return self._get_columns(visible=True)

    def _set_columns(self, selected_columns):
        for column in self.columns:
            if column.name not in [*selected_columns, *self.exempt_columns]:
                self.columns.hide(column.name)
            elif column.name in self.exempt_columns and not column.visible:
                self.columns.show(column.name)

        base_column_names = set(self.columns.names())
        self.sequence = [
            *[c for c in selected_columns if c in base_column_names],
            *[c for c in base_column_names if c not in selected_columns]
        ]

        if 'pk' in self.sequence:
            self.sequence.remove('pk')
            self.sequence.insert(0, 'pk')

        if 'actions' in self.sequence:
            self.sequence.remove('actions')
            self.sequence.append('actions')

    def _apply_prefetching(self, columns=None):
        if not isinstance(self.data, TableQuerysetData):
            return

        select_related_fields = []
        prefetch_related_fields = []

        for column in self.columns.iterall():
            if columns is not None:
                if column.name not in columns:
                    continue
            elif not column.visible:
                continue

            # Add any explicit prefetches declared by the column class itself
            if hasattr(column.column, 'get_prefetch_fields'):
                prefetch_related_fields.extend(column.column.get_prefetch_fields())

            model = getattr(self.Meta, 'model', None)
            if model is None:
                continue
            accessor = column.accessor
            if accessor.startswith('custom_field_data__'):
                continue
            prefetch_path = []
            use_select_related = True

            # Normalize and split relation path using both dot (.) and double-underscore (__) separators
            path_parts = str(accessor).replace('__', '.').split('.')
            for field_name in path_parts:
                try:
                    field = model._meta.get_field(field_name)
                except Exception:
                    break
                if hasattr(field, 'remote_field') and field.remote_field:
                    prefetch_path.append(field_name)
                    # If this step in the path is a many-to-many or reverse foreign key relation,
                    # we must fetch this entire path branch using prefetch_related.
                    if getattr(field, 'many_to_many', False) or getattr(field, 'one_to_many', False):
                        use_select_related = False
                    model = field.remote_field.model
                else:
                    break

            if prefetch_path:
                full_path = '__'.join(prefetch_path)
                if use_select_related:
                    select_related_fields.append(full_path)
                else:
                    prefetch_related_fields.append(full_path)

        if select_related_fields:
            # Special optimization for AssetType: since AssetType.__str__ accesses its related manufacturer,
            # we must always select_related manufacturer when select_related-ing asset_type.
            extended_select = []
            for path in select_related_fields:
                extended_select.append(path)
                if path == 'asset_type':
                    extended_select.append('asset_type__manufacturer')
                elif path.endswith('__asset_type'):
                    extended_select.append(path + '__manufacturer')
            unique_select = list(set(extended_select))
            self.data.data = self.data.data.select_related(*unique_select)
        if prefetch_related_fields:
            unique_prefetch = list(set(prefetch_related_fields))
            self.data.data = self.data.data.prefetch_related(*unique_prefetch)



    def configure(self, request):
        columns = None
        ordering = None

        if request.user.is_authenticated and self.prefixed_order_by_field in request.GET:
            if request.GET[self.prefixed_order_by_field]:
                ordering = request.GET.getlist(self.prefixed_order_by_field)
            else:
                ordering = None

        if request.user.is_authenticated:
            try:
                from django.apps import apps
                UserPreference = apps.get_model('users', 'UserPreference')
                prefs = UserPreference.objects.filter(user=request.user).first()
                if prefs and prefs.data:
                    model = getattr(self.Meta, 'model', None)
                    if model:
                        app_label = model._meta.app_label
                        user_config = prefs.data.get('tables', {}).get(app_label, {}).get(self.name, {})
                        if columns is None:
                            cols_from_pref = user_config.get('columns')
                            # Treat empty list same as absent key — fall through
                            # to Meta.default_columns / Meta.fields below
                            if cols_from_pref:
                                columns = cols_from_pref
            except Exception:
                logger.debug("Error reading user column preferences for table %s", self.name)

        if columns is None:
            columns = getattr(self.Meta, 'default_columns', self.Meta.fields)

        self._set_columns(columns)

        if columns_param := request.GET.get('include_columns'):
            for column_name in columns_param.split(','):
                if column_name in self.columns.names():
                    self.columns.show(column_name)
        if exclude_columns := request.GET.get('exclude_columns'):
            exclude_columns = exclude_columns.split(',')
            for column_name in exclude_columns:
                if column_name in self.columns.names() and column_name not in self.exempt_columns:
                    self.columns.hide(column_name)

        self._apply_prefetching()
        if ordering is not None:
            self.order_by = ordering

        paginate = {
            'paginator_class': EnhancedPaginator,
            'per_page': get_paginate_count(request)
        }
        tables.RequestConfig(request, paginate).configure(self)


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


# --- Search Results Table ---
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


class PermissionGroupTable(BaseTable):
    name = tables.Column(linkify=True)
    description = tables.Column()
    permissions = tables.Column()

    class Meta(BaseTable.Meta):
        model = PermissionGroup
        fields = ('name', 'description', 'permissions')
        sequence = ('name', 'description', 'permissions')

    def render_permissions(self, value):
        if not value:
            return mark_safe('<span class="text-muted">&mdash;</span>')
        items = [f'<span class="badge bg-primary me-1">{k}</span>' for k, v in value.items() if v]
        return mark_safe(' '.join(items)) if items else mark_safe('<span class="text-muted">&mdash;</span>')


class JobTable(BaseTable):
    name = tables.Column(linkify=True)
    status = tables.Column(verbose_name='Status')
    created = tables.DateTimeColumn(verbose_name='Created At', format='Y-m-d H:i:s')
    started = tables.DateTimeColumn(verbose_name='Started At', format='Y-m-d H:i:s')
    completed = tables.DateTimeColumn(verbose_name='Completed At', format='Y-m-d H:i:s')

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
        fields = ('created_at', 'rule', 'subject', 'status', 'actions')
        sequence = ('created_at', 'rule', 'subject', 'status', 'actions')

    def render_status(self, value):
        color = 'secondary'
        if value == AlertLog.STATUS_ACTIVE:
            color = 'danger'
        elif value == AlertLog.STATUS_ACKNOWLEDGED:
            color = 'warning'
        elif value == AlertLog.STATUS_RESOLVED:
            color = 'success'
        return format_html('<span class="badge bg-{}">{}</span>', color, value.capitalize())



