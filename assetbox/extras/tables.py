# assetbox/extras/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from .models import Tag
from core.tables.columns import ActionsColumn
from core.tables.base import BaseTable

class TagTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('extras:tag_update', args=[A('pk')], verbose_name='Name')
    # You might want a column to show count of items tagged with this tag.
    # This can be complex depending on how Tags are related (GenericForeignKey?).
    # For now, let's omit the count.
    # item_count = tables.Column(verbose_name='Tagged Items', orderable=False, empty_values=())
    color = tables.TemplateColumn(
        template_code='<span class="badge" style="background-color: #{{ record.color }};">&nbsp;</span>',
        verbose_name='Color'
    )
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Tag
        fields = ('pk', 'name', 'slug', 'color', 'description', 'actions')
        default_columns = ('pk', 'name', 'color', 'description', 'actions')

    # def render_item_count(self, record):
    #     # Implement counting logic here if needed
    #     return record.taggit_taggeditem_items.count() # Example if using django-taggit's default related name 