import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.generic import View
from django.db.models import Count

from django_tables2 import RequestConfig

# Core generic views & helpers
from core.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectCloneView, ObjectBulkEditView, ObjectBulkDeleteView, ObjectImportView
)
from core.utils import get_paginate_count
from core.panels import Panel

# Assets bulk import forms
from assets.forms.import_forms import AccessoryBulkImportForm, ConsumableBulkImportForm

# Inventory models, forms, tables, filters
from .models import Accessory, Consumable, Kit, KitItem
from . import forms, tables, filters
from assets.models import Asset


class AccessoryListView(ObjectListView):
    queryset = Accessory.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = filters.AccessoryFilterSet
    filterset_form = forms.AccessoryFilterForm
    table = tables.AccessoryTable
    action_buttons = ('add',)


class AccessoryDetailView(ObjectDetailView):
    queryset = Accessory.objects.select_related('manufacturer').prefetch_related('tags', 'assignments__assigned_holder', 'assignments__assigned_location')
    template_name = 'assets/accessories/accessory_detail.html'

    layout = (
        ((Panel('info', 'Accessory Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        accessory = self.get_object()

        # Prepare assignments table
        assignments_table = tables.AccessoryAssignmentTable(accessory.assignments.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)
        context['assignments_table'] = assignments_table
        return context


class AccessoryEditView(ObjectEditView):
    queryset = Accessory.objects.all()
    model = Accessory
    model_form = forms.AccessoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:accessory_list'


class AccessoryDeleteView(ObjectDeleteView):
    queryset = Accessory.objects.all()
    model = Accessory
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:accessory_list')

    def post(self, request, *args, **kwargs):
        accessory = self.get_object()
        assignment_count = accessory.assignments.count()
        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete accessory '{accessory}': It has {assignment_count} active assignments."
            )
            return redirect(accessory.get_absolute_url())
        return super().post(request, *args, **kwargs)


class AccessoryCloneView(ObjectCloneView):
    model = Accessory
    model_form = forms.AccessoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:accessory_list'


class ConsumableListView(ObjectListView):
    queryset = Consumable.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = filters.ConsumableFilterSet
    filterset_form = forms.ConsumableFilterForm
    table = tables.ConsumableTable
    action_buttons = ('add',)


class ConsumableDetailView(ObjectDetailView):
    queryset = Consumable.objects.select_related('manufacturer').prefetch_related('tags', 'consumptions__assigned_holder', 'consumptions__assigned_location')
    template_name = 'assets/consumables/consumable_detail.html'

    layout = (
        ((Panel('info', 'Consumable Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        consumable = self.get_object()

        # Prepare consumptions table
        consumptions_table = tables.ConsumableAssignmentTable(consumable.consumptions.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(consumptions_table)
        context['consumptions_table'] = consumptions_table
        return context


class ConsumableEditView(ObjectEditView):
    queryset = Consumable.objects.all()
    model = Consumable
    model_form = forms.ConsumableForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:consumable_list'


class ConsumableDeleteView(ObjectDeleteView):
    queryset = Consumable.objects.all()
    model = Consumable
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:consumable_list')

    def post(self, request, *args, **kwargs):
        consumable = self.get_object()
        consumption_count = consumable.consumptions.count()
        if consumption_count > 0:
            messages.error(
                request,
                f"Cannot delete consumable '{consumable}': It has {consumption_count} historical consumption records."
            )
            return redirect(consumable.get_absolute_url())
        return super().post(request, *args, **kwargs)


class ConsumableCloneView(ObjectCloneView):
    model = Consumable
    model_form = forms.ConsumableForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:consumable_list'


class KitListView(ObjectListView):
    queryset = Kit.objects.all().annotate(item_count=Count('items'))
    filterset = filters.KitFilterSet
    filterset_form = forms.KitFilterForm
    table = tables.KitTable
    action_buttons = ('add',)


class KitDetailView(ObjectDetailView):
    queryset = Kit.objects.all().prefetch_related('items__asset_type', 'items__accessory', 'items__license__software')
    template_name = 'assets/kits/kit_detail.html'

    layout = (
        ((Panel('info', 'Kit Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Check availability of each kit item
        items_with_availability = []
        all_available = True
        
        for item in self.object.items.all():
            avail = 0
            if item.asset_type:
                avail = Asset.objects.filter(asset_type=item.asset_type, status__slug='available').count()
                if avail < 1:
                    all_available = False
            elif item.accessory:
                avail = item.accessory.remaining_qty
                if avail < item.qty:
                    all_available = False
            elif item.license:
                avail = item.license.available_seats
                if avail < 1:
                    all_available = False
            
            items_with_availability.append({
                'item': item,
                'available_count': avail,
                'is_available': (avail >= (item.qty if item.accessory else 1))
            })
            
        context['items_with_availability'] = items_with_availability
        context['all_available'] = all_available
        return context


class KitEditView(ObjectEditView):
    queryset = Kit.objects.all()
    model = Kit
    model_form = forms.KitForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:kit_list'


class KitDeleteView(ObjectDeleteView):
    queryset = Kit.objects.all()
    model = Kit
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:kit_list')


class KitItemEditView(ObjectEditView):
    queryset = KitItem.objects.all()
    model = KitItem
    model_form = forms.KitItemForm
    template_name = 'generic/object_edit.html'

    def get_initial(self):
        initial = super().get_initial()
        kit_id = self.request.GET.get('kit')
        if kit_id:
            initial['kit'] = kit_id
        return initial

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse('assets:kit_list')


class KitItemDeleteView(ObjectDeleteView):
    queryset = KitItem.objects.all()
    model = KitItem
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse('assets:kit_list')


class KitCheckoutView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.core.exceptions import ValidationError
        kit = get_object_or_404(Kit, pk=pk)
        form = forms.KitCheckoutForm(request.POST)
        
        if not form.is_valid():
            if request.htmx:
                context = {
                    'form': form,
                    'kit': kit,
                }
                return render(request, "assets/includes/kit_checkout_modal.html#checkout-modal-form", context)
            return HttpResponseBadRequest("Invalid checkout form data.")

        holder = form.cleaned_data.get('assigned_holder')
        location = form.cleaned_data.get('assigned_location')
        notes = form.cleaned_data.get('notes') or ''

        try:
            from assets.services import checkout_kit
            checkout_kit(
                kit,
                holder=holder,
                location=location,
                user=request.user,
                notes=notes
            )

            messages.success(request, f"Kit '{kit.name}' checked out successfully.")
            
            if request.htmx:
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "closeModalEvent": None,
                    "kitListUpdated": None,
                    "showMessage": {"message": f"Kit '{kit.name}' checked out successfully.", "level": "success"}
                })
                return response
            return redirect(kit.get_absolute_url())

        except ValidationError as e:
            form.add_error(None, e.message)
            if request.htmx:
                context = {
                    'form': form,
                    'kit': kit,
                }
                return render(request, "assets/includes/kit_checkout_modal.html#checkout-modal-form", context)
            return render(request, "assets/includes/kit_checkout_modal.html", {'form': form, 'kit': kit})


@login_required
def accessory_checkout(request, pk):
    accessory = get_object_or_404(Accessory, pk=pk)
    
    if not accessory.allow_overallocate and accessory.remaining_qty <= 0:
        return HttpResponse("No stock available for checkout.", status=403)

    if request.method == 'POST':
        form = forms.AccessoryCheckoutForm(request.POST, accessory=accessory)
        if form.is_valid():
            from assets.services import checkout_accessory
            holder = form.cleaned_data.get('assigned_holder')
            location = form.cleaned_data.get('assigned_location')
            qty = form.cleaned_data.get('qty')
            notes = form.cleaned_data.get('notes')
            
            try:
                checkout_accessory(
                    accessory,
                    qty,
                    holder=holder,
                    location=location,
                    user=request.user,
                    notes=notes
                )
                recipient = holder or location
                messages.success(request, f"Checked out {qty}x '{accessory}' successfully to {recipient}.")
                
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "closeModalEvent": None,
                    "assetListUpdated": None,
                    "showMessage": {"message": f"Checked out {qty}x '{accessory}' successfully to {recipient}.", "level": "success"}
                })
                return response
            except Exception as e:
                form.add_error(None, str(e))
                context = {'form': form, 'accessory': accessory, 'request': request}
                return render(request, "assets/includes/accessory_checkout_modal.html#checkout-modal-form", context)
        else:
            context = {'form': form, 'accessory': accessory, 'request': request}
            return render(request, "assets/includes/accessory_checkout_modal.html#checkout-modal-form", context)
    else:
        form = forms.AccessoryCheckoutForm(accessory=accessory)

    context = {'form': form, 'accessory': accessory}
    return render(request, 'assets/includes/accessory_checkout_modal.html', context)


@login_required
@require_POST
def accessory_checkin(request, pk):
    from assets.services import checkin_accessory
    accessory, qty, recipient = checkin_accessory(pk, user=request.user)
    messages.success(request, f"Checked in {qty}x '{accessory}' from {recipient}.")
    return redirect(accessory.get_absolute_url())


@login_required
def consumable_checkout(request, pk):
    consumable = get_object_or_404(Consumable, pk=pk)
    
    if not consumable.allow_overallocate and consumable.remaining_qty <= 0:
        return HttpResponse("No stock available for consumption checkout.", status=403)

    if request.method == 'POST':
        form = forms.ConsumableCheckoutForm(request.POST, consumable=consumable)
        if form.is_valid():
            from assets.services import checkout_consumable
            holder = form.cleaned_data.get('assigned_holder')
            location = form.cleaned_data.get('assigned_location')
            qty = form.cleaned_data.get('qty')
            notes = form.cleaned_data.get('notes')
            
            try:
                checkout_consumable(
                    consumable,
                    qty,
                    holder=holder,
                    location=location,
                    user=request.user,
                    notes=notes
                )
                recipient = holder or location
                messages.success(request, f"Checked out / Consumed {qty}x '{consumable}' for {recipient}.")
                
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "closeModalEvent": None,
                    "assetListUpdated": None,
                    "showMessage": {"message": f"Consumed {qty}x '{consumable}' for {recipient}.", "level": "success"}
                })
                return response
            except Exception as e:
                form.add_error(None, str(e))
                context = {'form': form, 'consumable': consumable, 'request': request}
                return render(request, "assets/includes/consumable_checkout_modal.html#checkout-modal-form", context)
        else:
            context = {'form': form, 'consumable': consumable, 'request': request}
            return render(request, "assets/includes/consumable_checkout_modal.html#checkout-modal-form", context)
    else:
        form = forms.ConsumableCheckoutForm(consumable=consumable)

    context = {'form': form, 'consumable': consumable}
    return render(request, 'assets/includes/consumable_checkout_modal.html', context)


@login_required
def kit_checkout_modal(request, pk):
    kit = get_object_or_404(Kit, pk=pk)
    
    # Check if kit has items
    if not kit.items.exists():
        return HttpResponse("This kit has no items to check out.", status=400)

    if request.method == 'POST':
        # Let KitCheckoutView handle it
        return KitCheckoutView.as_view()(request, pk=pk)
    else:
        form = forms.KitCheckoutForm()

    context = {'form': form, 'kit': kit}
    return render(request, 'assets/includes/kit_checkout_modal.html', context)


class AccessoryImportView(ObjectImportView):
    model_form = AccessoryBulkImportForm


class ConsumableImportView(ObjectImportView):
    model_form = ConsumableBulkImportForm


class AccessoryBulkEditView(ObjectBulkEditView):
    queryset = Accessory.objects.all()


class AccessoryBulkDeleteView(ObjectBulkDeleteView):
    queryset = Accessory.objects.all()


class ConsumableBulkEditView(ObjectBulkEditView):
    queryset = Consumable.objects.all()


class ConsumableBulkDeleteView(ObjectBulkDeleteView):
    queryset = Consumable.objects.all()
