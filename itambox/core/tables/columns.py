import django_tables2 as tables
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.urls import reverse, NoReverseMatch
from core.utils import get_model_viewname

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
                f'{button}'
                f'<a class="btn btn-sm btn-{dropdown_class} dropdown-toggle dropdown-toggle-split" type="button" data-bs-toggle="dropdown" aria-expanded="false">'
                f'</a>'
                f'<ul class="dropdown-menu dropdown-menu-end">{"".join(dropdown_links)}</ul>'
                f'</span>'
            )
        elif button:
            html += button
        elif dropdown_links:
            html += (
                f'<span class="btn-group dropdown">'
                f'<a class="btn btn-sm btn-secondary dropdown-toggle dropdown-toggle-split" type="button" data-bs-toggle="dropdown" aria-expanded="false">'
                f'</a>'
                f'<ul class="dropdown-menu dropdown-menu-end">{"".join(dropdown_links)}</ul>'
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
