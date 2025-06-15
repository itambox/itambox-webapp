import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from assets.models import Manufacturer # Keep import for potential linking
# from core.tables import BaseTable, TagColumn, BooleanColumn # Remove old import
from core.tables import BaseTable, BooleanColumn # Import only needed core components
from extras.tables import TagColumn # Import TagColumn from extras
from .models import Software

class SoftwareTable(BaseTable):
    """Table for displaying Software instances."""
    pk = tables.CheckBoxColumn(accessor='pk', attrs = { "th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn(
        viewname='software:software_detail', # Use the correct viewname once URLs are set
        args=[tables.A('pk')] 
    )
    manufacturer = tables.LinkColumn(
        viewname='assets:manufacturer_detail', # Link to Manufacturer detail view
        args=[tables.A('manufacturer__pk')],
        accessor='manufacturer.name',
        verbose_name=_("Manufacturer")
    )
    tags = TagColumn(
        url_name='software:software_list' # Link back to the list view filtered by tag
    )

    class Meta(BaseTable.Meta):
        model = Software
        fields = ('pk', 'name', 'manufacturer', 'description', 'tags', 'created_at', 'updated_at')
        default_columns = ('pk', 'name', 'manufacturer', 'description', 'tags') 