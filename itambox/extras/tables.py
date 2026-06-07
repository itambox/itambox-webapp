# itambox/extras/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from django.utils.safestring import mark_safe
from django.urls import reverse
from django.utils.html import escape, format_html
from .models import Tag, CustomField, CustomFieldset
from core.tables import ActionsColumn, BaseTable, ToggleColumn

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
    name = tables.LinkColumn('extras:tag_detail', args=[A('pk')], verbose_name='Name')
    # You might want a column to show count of items tagged with this tag.
    # This can be complex depending on how Tags are related (GenericForeignKey?).
    # For now, let's omit the count.
    # item_count = tables.Column(verbose_name='Tagged Items', orderable=False, empty_values=())
    color = tables.Column(verbose_name='Color', orderable=True)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Tag
        fields = ('pk', 'name', 'slug', 'color', 'description', 'actions')
        default_columns = ('pk', 'name', 'color', 'description', 'actions')

    def render_color(self, value):
        if value:
            return mark_safe(f'<span class="badge" style="background-color: #{value};">&nbsp;</span> #{value}')
        return "—"


class CustomFieldTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:customfield_detail', args=[A('pk')], verbose_name='Name')
    label = tables.Column(verbose_name='Label')
    field_type = tables.Column(verbose_name='Field Type')
    required = tables.BooleanColumn(verbose_name='Required')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = CustomField
        fields = ('pk', 'name', 'label', 'field_type', 'required', 'actions')
        default_columns = ('pk', 'name', 'label', 'field_type', 'required', 'actions')


class CustomFieldsetTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:customfieldset_detail', args=[A('pk')], verbose_name='Name')
    fields_count = tables.Column(verbose_name='Fields Count', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = CustomFieldset
        fields = ('pk', 'name', 'fields_count', 'actions')
        default_columns = ('pk', 'name', 'fields_count', 'actions')

    def render_fields_count(self, value, record=None):
        return value or 0


