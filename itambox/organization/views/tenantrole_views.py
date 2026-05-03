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
        from ..forms.tenantrole_form import MATRIX_MODELS
        groups = {}
        for key, info in MATRIX_MODELS.items():
            group_name = info.get('group', 'Other')
            if group_name not in groups:
                groups[group_name] = []
            app = info['app']
            model = info['model_name']
            groups[group_name].append({
                'label': info['label'],
                'read_codename': f'{app}.view_{model}',
                'create_codename': f'{app}.add_{model}',
                'edit_codename': f'{app}.change_{model}',
                'delete_codename': f'{app}.delete_{model}',
            })
        context['matrix_grouped_items'] = groups
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
