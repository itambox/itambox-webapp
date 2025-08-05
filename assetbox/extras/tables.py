# assetbox/extras/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from django.utils.safestring import mark_safe
from django.urls import reverse
from django.utils.html import escape, format_html
from .models import Tag
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
        # Default transform to render tags with links
        kwargs.setdefault('transform', self.render_tags)
        # Prevent default linking of ManyToManyColumn
        kwargs.setdefault('linkify_item', False) 
        super().__init__(*args, **kwargs)

    def render_tags(self, value):
        url = reverse(self.url_name or 'extras:tag_list') + '?tag=' + escape(value.slug)
        return format_html(
            '<a href="{}" class="badge bg-primary me-1" style="background-color: #{};">{}</a>',
            url, value.color, value.name
        )

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

