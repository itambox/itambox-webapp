from django.urls import reverse_lazy
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)
from ..models import TenantRole
from ..forms import TenantRoleForm
from ..tables import TenantRoleTable

class TenantRoleListView(ObjectListView):
    queryset = TenantRole.objects.all()
    table = TenantRoleTable
    action_buttons = ('add',)

class TenantRoleDetailView(ObjectDetailView):
    queryset = TenantRole.objects.all()
    template_name = 'organization/tenantroles/tenantrole_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['matrix_models'] = {
            'asset': {
                'label': 'Assets',
                'read_codename': 'assets.view_asset',
                'write_codename': 'assets.change_asset',
                'delete_codename': 'assets.delete_asset',
            },
            'accessory': {
                'label': 'Accessories',
                'read_codename': 'inventory.view_accessory',
                'write_codename': 'inventory.change_accessory',
                'delete_codename': 'inventory.delete_accessory',
            },
            'consumable': {
                'label': 'Consumables',
                'read_codename': 'inventory.view_consumable',
                'write_codename': 'inventory.change_consumable',
                'delete_codename': 'inventory.delete_consumable',
            },
            'kit': {
                'label': 'Kits',
                'read_codename': 'inventory.view_kit',
                'write_codename': 'inventory.change_kit',
                'delete_codename': 'inventory.delete_kit',
            },
            'component': {
                'label': 'Components',
                'read_codename': 'components.view_component',
                'write_codename': 'components.change_component',
                'delete_codename': 'components.delete_component',
            },
            'location': {
                'label': 'Locations',
                'read_codename': 'organization.view_location',
                'write_codename': 'organization.change_location',
                'delete_codename': 'organization.delete_location',
            },
            'site': {
                'label': 'Sites',
                'read_codename': 'organization.view_site',
                'write_codename': 'organization.change_site',
                'delete_codename': 'organization.delete_site',
            },
            'assetholder': {
                'label': 'Asset Holders',
                'read_codename': 'organization.view_assetholder',
                'write_codename': 'organization.change_assetholder',
                'delete_codename': 'organization.delete_assetholder',
            },
        }
        return context

class TenantRoleEditView(ObjectEditView):
    queryset = TenantRole.objects.all()
    model = TenantRole
    model_form = TenantRoleForm
    template_name = 'organization/tenantrole_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['tenant'] = getattr(self.request, 'active_tenant', None)
        return kwargs

class TenantRoleDeleteView(ObjectDeleteView):
    queryset = TenantRole.objects.all()
    model = TenantRole
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:tenantrole_list')
