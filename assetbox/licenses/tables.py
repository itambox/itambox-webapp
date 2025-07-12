import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from core.tables import BaseTable, ActionsColumn, ToggleColumn
from extras.tables import TagColumn
from .models import License, LicenseSeatAssignment

class LicenseTable(BaseTable):
    """Table for displaying License entitlements."""
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn(
        viewname='licenses:license_detail',
        args=[tables.A('pk')]
    )
    software = tables.LinkColumn(
        viewname='software:software_detail',
        args=[tables.A('software__pk')],
        accessor='software.name',
        verbose_name=_("Software")
    )
    license_type = tables.Column(verbose_name=_("Type"))
    seats = tables.Column(verbose_name=_("Total Seats"))
    available_seats = tables.Column(verbose_name=_("Available Seats"), orderable=False)
    tenant = tables.LinkColumn('organization:tenant_detail', args=[tables.A('tenant.pk')], accessor='tenant.name', verbose_name=_("Tenant"))
    expiration_date = tables.DateColumn(verbose_name=_("Expiration Date"), format='Y-m-d')
    tags = TagColumn(url_name='licenses:license_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = License
        fields = ('pk', 'name', 'software', 'tenant', 'license_type', 'seats', 'available_seats', 'purchase_date', 'expiration_date', 'tags', 'actions')
        default_columns = ('pk', 'name', 'software', 'tenant', 'license_type', 'seats', 'available_seats', 'expiration_date', 'actions')

class LicenseSeatAssignmentTable(BaseTable):
    """Table for displaying individual License Seat Assignments."""
    pk = ToggleColumn(accessor='pk')
    license = tables.LinkColumn(
        viewname='licenses:license_detail',
        args=[tables.A('license__pk')],
        accessor='license.name',
        verbose_name=_("License")
    )
    asset = tables.LinkColumn(
        viewname='assets:asset_detail',
        args=[tables.A('asset__pk')],
        accessor='asset.name',
        verbose_name=_("Asset")
    )
    assigned_holder = tables.LinkColumn(
        viewname='organization:assetholder_detail',
        args=[tables.A('assigned_holder__pk')],
        accessor='assigned_holder.upn',
        verbose_name=_("Asset Holder")
    )
    assigned_date = tables.DateTimeColumn(verbose_name=_("Assigned At"), format='Y-m-d H:i')
    notes = tables.Column(verbose_name=_("Notes"))
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = LicenseSeatAssignment
        fields = ('pk', 'license', 'asset', 'assigned_holder', 'assigned_date', 'notes', 'actions')
        default_columns = ('pk', 'license', 'asset', 'assigned_holder', 'assigned_date', 'actions')
