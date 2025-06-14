# assetbox/extras/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from django.utils.safestring import mark_safe
from .models import Tag, ConfigTemplate
from core.tables import ActionsColumn, BaseTable

class TagTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk')
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

    # def render_item_count(self, record):
    #     # Implement counting logic here if needed
    #     return record.taggit_taggeditem_items.count() # Example if using django-taggit's default related name 

class ConfigTemplateTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk')
    name = tables.LinkColumn('extras:configtemplate_detail', args=[A('pk')], verbose_name='Name')
    asset_roles_count = tables.Column(verbose_name='Asset Roles', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ConfigTemplate
        fields = ('pk', 'name', 'description', 'asset_roles_count', 'actions')
        default_columns = ('pk', 'name', 'description', 'asset_roles_count', 'actions')
    
    def render_asset_roles_count(self, record):
        return record.asset_roles.count() 