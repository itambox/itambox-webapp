import json
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.utils.html import format_html
from django.views.generic import View
from django.utils.translation import gettext_lazy as _

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectCloneView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView
from itambox.panels import Panel

from ..models import Accessory, Kit, AccessoryStock, AccessoryAssignment
from .. import forms, tables, filters
from inventory.services import checkout_inventory_item, checkin_accessory


class AccessoryListView(ObjectListView):
    queryset = Accessory.objects.with_counts().select_related('tenant', 'manufacturer', 'category').prefetch_related('tags')
    filterset = filters.AccessoryFilterSet
    filterset_form = forms.AccessoryFilterForm
    table = tables.AccessoryTable
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Accessories')
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (None, _('Inventory & Stock')),
            (None, _('Accessories'))
        ]
        if not (self.is_htmx_partial() and self.content_partial_name):
            from organization.models import AssetHolder, Location
            from assets.models import Asset
            context['asset_holders'] = AssetHolder.objects.all().order_by('last_name', 'first_name')
            context['locations'] = Location.objects.all().order_by('name')
            context['assets'] = Asset.objects.all().order_by('asset_tag')
        return context


class AccessoryDetailView(ObjectDetailView):
    queryset = Accessory.objects.select_related('manufacturer').prefetch_related('tags', 'assignments__assigned_holder', 'assignments__assigned_location', 'stocks__location')
    template_name = 'assets/accessories/accessory_detail.html'

    layout = (
        ((Panel('metrics', _('Metrics Overview')),),),
        ((Panel('info', _('Accessory Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        accessory = self.get_object()

        assignments_table = tables.AccessoryAssignmentTable(accessory.assignments.all(), request=self.request)
        assignments_table.configure(self.request)
        context['assignments_table'] = assignments_table

        stocks_table = tables.AccessoryStockTable(accessory.stocks.all(), request=self.request)
        stocks_table.configure(self.request)
        context['stocks_table'] = stocks_table

        # Kits
        kits_qs = Kit.objects.filter(items__accessory=accessory).distinct()
        kits_table = tables.KitTable(kits_qs, request=self.request)
        kits_table.configure(self.request)
        context['kits_table'] = kits_table

        return context



class AccessoryEditView(ObjectEditView):
    queryset = Accessory.objects.all()
    model = Accessory
    model_form = forms.AccessoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:accessory_list'


class AccessoryDeleteView(ObjectDeleteView):
    queryset = Accessory.objects.all()
    model = Accessory
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('inventory:accessory_list')

    def post(self, request, *args, **kwargs):
        accessory = self.get_object()
        assignment_count = accessory.assignments.count()
        if assignment_count > 0:
            messages.error(
                request,
                _("Cannot delete accessory '%(accessory)s': It has %(count)s active assignments.") % {"accessory": accessory, "count": assignment_count}
            )
            return redirect(accessory.get_absolute_url())
        return super().post(request, *args, **kwargs)


class AccessoryCloneView(ObjectCloneView):
    model = Accessory
    model_form = forms.AccessoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:accessory_list'


class AccessoryCheckoutView(GenericTransactionView):
    permission_required = ('inventory.change_accessory',)
    queryset = Accessory.objects.all()
    model_form = forms.AccessoryCheckoutForm
    service_callable = checkout_inventory_item
    context_object_name = 'accessory'
    template_name = 'inventory/includes/accessory_checkout_modal.html'
    error_partial = 'inventory/includes/accessory_checkout_modal.html#checkout-modal-form'
    success_message = _("Accessory checked out successfully.")
    form_field_map = {
        'assigned_holder': 'holder',
        'assigned_location': 'location',
        'assigned_asset': 'asset',
        'from_location': 'source_location',
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs['instance']
        kwargs['accessory'] = self.get_object()
        if 'initial' not in kwargs:
            kwargs['initial'] = {}
        for key in self.request.GET:
            kwargs['initial'][key] = self.request.GET.get(key)
        return kwargs


class AccessoryCheckinView(SimplePostView):
    permission_required = ('inventory.change_accessory',)
    queryset = AccessoryAssignment.objects.all()

    def perform_action(self, assignment, request):
        accessory, qty, recipient = checkin_accessory(assignment.pk, user=request.user)
        return {
            'message': str(_("Checked in %(qty)sx '%(accessory)s' from %(recipient)s.") % {"qty": qty, "accessory": accessory, "recipient": recipient}),
            'redirect': accessory.get_absolute_url(),
        }

    def get_success_redirect(self, obj, result):
        return redirect(result.get('redirect') or '/')


class AccessoryBulkEditView(ObjectBulkEditView):
    queryset = Accessory.objects.all()


class AccessoryBulkDeleteView(ObjectBulkDeleteView):
    queryset = Accessory.objects.all()


class AccessoryStockListView(ObjectListView):
    queryset = AccessoryStock.objects.select_related('accessory', 'location').all()
    table = tables.AccessoryStockTable
    action_buttons = ('add',)
    filterset = filters.AccessoryStockFilterSet
    filterset_form = forms.AccessoryStockFilterForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Accessory Stocks')
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (reverse('inventory:accessory_list'), _('Accessories')),
            (None, _('Stocks'))
        ]
        if not (self.is_htmx_partial() and self.content_partial_name):
            from organization.models import AssetHolder, Location
            from assets.models import Asset
            context['asset_holders'] = AssetHolder.objects.all().order_by('last_name', 'first_name')
            context['locations'] = Location.objects.all().order_by('name')
            context['assets'] = Asset.objects.all().order_by('asset_tag')
        return context


class AccessoryStockEditView(ObjectEditView):
    queryset = AccessoryStock.objects.all()
    model = AccessoryStock
    model_form = forms.AccessoryStockForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:accessory_list'


class AccessoryStockDeleteView(ObjectDeleteView):
    queryset = AccessoryStock.objects.all()
    model = AccessoryStock
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('inventory:accessory_list')


class AccessoryAssignmentListView(ObjectListView):
    queryset = AccessoryAssignment.objects.select_related(
        'accessory', 'assigned_holder', 'assigned_location', 'assigned_asset'
    ).all()
    table = tables.AccessoryAssignmentTable
    action_buttons = ()
    filterset = filters.AccessoryAssignmentFilterSet
    filterset_form = forms.AccessoryAssignmentFilterForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Accessory Assignments')
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (reverse('inventory:accessory_list'), _('Accessories')),
            (None, _('Assignments'))
        ]
        return context


class AccessoryStockAdjustView(LoginRequiredMixin, View):
    def post(self, request, pk):
        with transaction.atomic():
            try:
                stock = AccessoryStock.objects.select_for_update().get(pk=pk)
            except AccessoryStock.DoesNotExist:
                raise Http404
            if not request.user.has_perm('inventory.change_accessorystock', obj=stock.accessory):
                return HttpResponseForbidden(_("Permission denied."))
            action = request.GET.get('action')

            if action == 'increment':
                stock.qty += 1
                stock.save()
            elif action == 'decrement':
                if stock.qty > 0:
                    stock.qty -= 1
                    stock.save()

        return HttpResponse(format_html(
            '<div class="d-flex align-items-center justify-content-start">'
            '  <button class="btn btn-sm btn-icon btn-outline-secondary me-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div" style="height: 1.5rem; width: 1.5rem;">'
            '    <i class="mdi mdi-minus" style="font-size: 0.75rem;"></i>'
            '  </button>'
            '  <span class="badge bg-blue-lt text-blue font-weight-bold px-2 py-1" style="font-size: 0.85rem;">{}</span>'
            '  <button class="btn btn-sm btn-icon btn-outline-secondary ms-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div" style="height: 1.5rem; width: 1.5rem;">'
            '    <i class="mdi mdi-plus" style="font-size: 0.75rem;"></i>'
            '  </button>'
            '</div>',
            reverse('inventory:accessorystock_adjust', kwargs={'pk': stock.pk}) + '?action=decrement',
            stock.qty,
            reverse('inventory:accessorystock_adjust', kwargs={'pk': stock.pk}) + '?action=increment'
        ))


class AccessoryStockCreateModalView(LoginRequiredMixin, View):
    def get(self, request, pk):
        accessory = get_object_or_404(Accessory, pk=pk)
        from ..forms import AccessoryStockModalForm
        initial = {}
        location_id = request.GET.get('location')
        if location_id:
            initial['location'] = location_id
        form = AccessoryStockModalForm(initial=initial)
        return render(request, 'generic/includes/add_stock_modal.html', {
            'object': accessory,
            'form': form,
            'post_url': reverse('inventory:accessory_add_stock', kwargs={'pk': accessory.pk}),
        })

    def post(self, request, pk):
        accessory = get_object_or_404(Accessory, pk=pk)
        from ..forms import AccessoryStockModalForm
        form = AccessoryStockModalForm(request.POST)
        if form.is_valid():
            stock = form.save(commit=False)
            stock.accessory = accessory
            stock.save()
            if request.headers.get('HX-Request'):
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "closeModalEvent": None,
                    "tableRefreshRequired": None,
                    "showMessage": {
                        "message": str(_("Added stock pool for %(location)s.") % {"location": stock.location}),
                        "level": "success"
                    }
                })
                return response
            return redirect(accessory.get_absolute_url())

        return render(request, 'generic/includes/add_stock_modal.html', {
            'object': accessory,
            'form': form,
            'post_url': reverse('inventory:accessory_add_stock', kwargs={'pk': accessory.pk}),
        })
