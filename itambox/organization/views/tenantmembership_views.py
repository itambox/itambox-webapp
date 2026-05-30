from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.contrib import messages

from itambox.views.generic import ObjectListView, ObjectEditView, ObjectDeleteView
from ..models import TenantMembership
from ..forms import TenantMembershipForm
from ..tables import TenantMembershipTable

class TenantMembershipListView(ObjectListView):
    queryset = TenantMembership.objects.all()
    table = TenantMembershipTable
    action_buttons = ('add',)

class TenantMembershipCreateView(ObjectEditView):
    queryset = TenantMembership.objects.all()
    model = TenantMembership
    model_form = TenantMembershipForm
    template_name = 'generic/object_edit.html'

    def get_initial(self):
        initial = super().get_initial()
        user_pk = self.request.GET.get('user')
        if user_pk:
            initial['user'] = user_pk
        tenant_pk = self.request.GET.get('tenant')
        if tenant_pk:
            initial['tenant'] = tenant_pk
        return initial

    def get_success_url(self):
        if self.instance and self.instance.user:
            return reverse('users:user_detail', kwargs={'pk': self.instance.user.pk})
        user_pk = self.request.GET.get('user')
        if user_pk:
            return reverse('users:user_detail', kwargs={'pk': user_pk})
        return reverse('users:user_list')

class TenantMembershipDeleteView(ObjectDeleteView):
    queryset = TenantMembership.objects.all()
    model = TenantMembership
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        membership = self.get_object()
        return reverse('users:user_detail', kwargs={'pk': membership.user.pk})
