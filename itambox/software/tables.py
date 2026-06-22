import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from assets.models import Manufacturer
from core.tables import BaseTable, BooleanColumn, ToggleColumn, ActionsColumn
from extras.tables import TagColumn
from .models import Software, InstalledSoftware

from django.urls import reverse
from django.utils.html import format_html

class SoftwareTable(BaseTable):
    """Table for displaying Software instances."""
    pk = ToggleColumn(accessor='pk')
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
    license_count = tables.Column(verbose_name=_("Licenses"), orderable=False)
    installed_count = tables.Column(verbose_name=_("Installs"), orderable=False)
    tags = TagColumn(
        url_name='software:software_list' # Link back to the list view filtered by tag
    )
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Software
        fields = ('pk', 'name', 'manufacturer', 'license_count', 'installed_count', 'description', 'tags', 'created_at', 'updated_at', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'license_count', 'installed_count', 'description', 'tags', 'actions')

    def render_license_count(self, value, record=None):
        if record and value:
            url = f"{reverse('licenses:license_list')}?software={record.pk}"
            return format_html('<a href="{}">{}</a>', url, value)
        return value or 0

    def render_installed_count(self, value, record=None):
        if record and value:
            url = f"{reverse('software:software_detail', args=[record.pk])}#installed-instances"
            return format_html('<a href="{}">{}</a>', url, value)
        return value or 0

class InstalledSoftwareTable(BaseTable):
    """Table for displaying InstalledSoftware instances."""
    asset = tables.LinkColumn(
        viewname='assets:asset_detail',
        args=[tables.A('asset__pk')],
        accessor='asset.name',
        verbose_name=_("Asset")
    )
    software = tables.LinkColumn(
        viewname='software:software_detail',
        args=[tables.A('software__pk')],
        accessor='software.name',
        verbose_name=_("Software")
    )
    version_detected = tables.Column(verbose_name=_("Version"))
    install_date = tables.DateColumn(verbose_name=_("Install Date"), format='Y-m-d')
    last_seen_date = tables.DateTimeColumn(verbose_name=_("Last Seen"), format='Y-m-d H:i')

    class Meta(BaseTable.Meta):
        model = InstalledSoftware
        fields = ('asset', 'software', 'version_detected', 'install_date', 'last_seen_date')
        default_columns = ('asset', 'software', 'version_detected', 'install_date', 'last_seen_date') 