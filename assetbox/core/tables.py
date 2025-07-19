import logging
import django_tables2 as tables
from django_tables2.utils import A
from django_tables2.data import TableQuerysetData
from .models import ObjectChange
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
        '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-circle-check" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round">'
        '<path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0" /><path d="M9 12l2 2l4 -4" /></svg>'
        '</span>'
    )
    FALSE_MARK = mark_safe(
        '<span class="text-danger">'
        '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-circle-x" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round">'
        '<path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0" /><path d="M10 10l4 4m0 -4l-4 4" /></svg>'
        '</span>'
    )
    EMPTY_MARK = mark_safe('<span class="text-muted">&mdash;</span>')

    def __init__(self, *args, true_mark=None, false_mark=None, **kwargs):
        if true_mark is not None:
            self.true_mark = true_mark
        if false_mark is not None:
            self.false_mark = false_mark
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
                    'class': 'w-1',
                    'aria-label': _('Select all'),
                },
                'td': {
                    'class': 'w-1',
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
        'td': {
            'class': 'text-end text-nowrap noprint p-1'
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

        icon_edit = '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-pencil" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M4 20h4l10.5 -10.5a1.5 1.5 0 0 0 -4 -4l-10.5 10.5v4" /><path d="M13.5 6.5l4 4" /></svg>'
        icon_delete = '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-trash" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M4 7l16 0" /><path d="M10 11l0 6" /><path d="M14 11l0 6" /><path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12" /><path d="M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3" /></svg>'

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

        prefetch_fields = []
        for column in self.columns.iterall():
            if columns is not None:
                if column.name not in columns:
                    continue
            elif not column.visible:
                continue
            model = getattr(self.Meta, 'model', None)
            if model is None:
                continue
            accessor = column.accessor
            if accessor.startswith('custom_field_data__'):
                continue
            prefetch_path = []
            for field_name in accessor.split(accessor.SEPARATOR):
                try:
                    field = model._meta.get_field(field_name)
                except Exception:
                    break
                if hasattr(field, 'remote_field') and field.remote_field:
                    prefetch_path.append(field_name)
                    model = field.remote_field.model
                else:
                    break
            if prefetch_path:
                prefetch_fields.append('__'.join(prefetch_path))
        if prefetch_fields:
            self.data.data = self.data.data.prefetch_related(*prefetch_fields)

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
                            columns = user_config.get('columns')
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
