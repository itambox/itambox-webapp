from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse

from itambox.views.generic import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
from itambox.views.generic.service_views import SimplePostView
from .models import PurchaseOrder, PurchaseOrderLine
from .tables import PurchaseOrderTable
from .filters import PurchaseOrderFilterSet
from .forms import PurchaseOrderForm, PurchaseOrderLineForm

class PurchaseOrderListView(ObjectListView):
    queryset = PurchaseOrder.objects.all()
    filterset_class = PurchaseOrderFilterSet
    table_class = PurchaseOrderTable
    template_name = 'procurement/purchaseorder_list.html'
    permission_required = 'procurement.view_purchaseorder'

class PurchaseOrderDetailView(ObjectDetailView):
    queryset = PurchaseOrder.objects.all()
    template_name = 'procurement/purchaseorder_detail.html'
    permission_required = 'procurement.view_purchaseorder'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lines = self.object.lines.all()
        context['lines'] = lines
        context['line_form'] = PurchaseOrderLineForm()
        
        # Calculate total value of the PO
        from decimal import Decimal
        total_value = Decimal('0.00')
        for line in lines:
            if line.unit_price and line.qty_ordered:
                total_value += line.unit_price * line.qty_ordered
        context['total_value'] = total_value
        
        # Fetch linked fulfillment links
        from procurement.models import FulfillmentLink
        context['fulfillment_links'] = FulfillmentLink.objects.filter(
            purchase_order_line__purchase_order=self.object
        ).select_related('asset_request', 'purchase_order_line')
        
        return context

class PurchaseOrderEditView(ObjectEditView):
    queryset = PurchaseOrder.objects.all()
    model_form = PurchaseOrderForm
    permission_required = 'procurement.change_purchaseorder'

    def get_initial(self):
        initial = super().get_initial()
        if 'from_request' in self.request.GET:
            from_request_id = self.request.GET.get('from_request')
            from assets.models import AssetRequest
            try:
                asset_request = AssetRequest.objects.get(pk=from_request_id)
                loc = asset_request.assigned_location or asset_request.source_location
                if loc:
                    initial['destination_location'] = loc.pk
            except AssetRequest.DoesNotExist:
                pass
        return initial

    def form_valid(self, form):
        is_creating = self.get_object() is None
        if is_creating:
            form.instance.created_by = self.request.user
        response = super().form_valid(form)
        if is_creating and 'from_request' in self.request.GET:
            from_request_id = self.request.GET.get('from_request')
            from assets.models import AssetRequest
            from procurement.models import PurchaseOrderLine, FulfillmentLink
            try:
                asset_request = AssetRequest.objects.get(pk=from_request_id)
                # Create the line item matching the request
                line = PurchaseOrderLine(
                    purchase_order=self.object,
                    qty_ordered=asset_request.qty,
                    tenant=self.object.tenant
                )
                if asset_request.asset_type:
                    line.asset_type = asset_request.asset_type
                elif asset_request.component:
                    line.component = asset_request.component
                elif asset_request.accessory:
                    line.accessory = asset_request.accessory
                elif asset_request.consumable:
                    line.consumable = asset_request.consumable
                line.save()
                
                # Create FulfillmentLink
                FulfillmentLink.objects.create(
                    tenant=self.object.tenant,
                    asset_request=asset_request,
                    purchase_order_line=line,
                    qty_allocated=asset_request.qty
                )
                
                # Transition request status to procurement
                asset_request.status = AssetRequest.STATUS_PROCUREMENT
                asset_request.save()
                
                messages.success(self.request, f"Linked Purchase Order to Asset Request #{asset_request.pk} and transitioned request status to Awaiting Procurement.")
            except Exception as e:
                messages.error(self.request, f"Failed to link PO to request: {e}")
        return response

class PurchaseOrderDeleteView(ObjectDeleteView):
    queryset = PurchaseOrder.objects.all()
    permission_required = 'procurement.delete_purchaseorder'
    default_return_url = reverse_lazy('procurement:purchaseorder_list')

from django.views import View
from django.contrib.auth.mixins import PermissionRequiredMixin

class PurchaseOrderLineAddView(PermissionRequiredMixin, View):
    permission_required = 'procurement.change_purchaseorder'

    def get(self, request, *args, **kwargs):
        po = get_object_or_404(PurchaseOrder, pk=kwargs.get('po_pk'))
        context = {
            'object': po,
            'lines': po.lines.all(),
            'line_form': PurchaseOrderLineForm(),
        }
        return render(request, 'procurement/includes/purchaseorder_lines_container.html', context)

    def post(self, request, *args, **kwargs):
        po = get_object_or_404(PurchaseOrder, pk=kwargs.get('po_pk'))
        if po.status != PurchaseOrder.STATUS_DRAFT:
            messages.error(request, "Line items can only be added while the purchase order is in Draft status.")
            return redirect(po.get_absolute_url())
        form = PurchaseOrderLineForm(request.POST)
        if form.is_valid():
            line = form.save(commit=False)
            line.purchase_order = po
            # Handle tenant scoping if applicable
            if po.tenant:
                line.tenant = po.tenant
            line.save()
            messages.success(request, "Line item added successfully.")
            form = PurchaseOrderLineForm()
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        
        if request.headers.get('HX-Request'):
            context = {
                'object': po,
                'lines': po.lines.all(),
                'line_form': form,
            }
            return render(request, 'procurement/includes/purchaseorder_lines_container.html', context)
        return redirect(po.get_absolute_url())

class PurchaseOrderLineDeleteView(ObjectDeleteView):
    queryset = PurchaseOrderLine.objects.all()
    permission_required = 'procurement.change_purchaseorder'

    def get_permission_required(self):
        return (self.permission_required,)

    def get_return_url(self, request, obj):
        return obj.purchase_order.get_absolute_url()

    def post(self, request, *args, **kwargs):
        if request.headers.get('HX-Request'):
            obj = self.get_object()
            po = obj.purchase_order
            if po.status != PurchaseOrder.STATUS_DRAFT:
                messages.error(request, "Line items can only be removed while the purchase order is in Draft status.")
                context = {
                    'object': po,
                    'lines': po.lines.all(),
                    'line_form': PurchaseOrderLineForm(),
                }
                return render(request, 'procurement/includes/purchaseorder_lines_container.html', context)
            obj.delete()
            messages.success(request, "Line item deleted successfully.")
            context = {
                'object': po,
                'lines': po.lines.all(),
                'line_form': PurchaseOrderLineForm(),
            }
            return render(request, 'procurement/includes/purchaseorder_lines_container.html', context)
        return super().post(request, *args, **kwargs)

class PurchaseOrderLineEditView(PermissionRequiredMixin, View):
    permission_required = 'procurement.change_purchaseorder'

    def get(self, request, *args, **kwargs):
        line = get_object_or_404(PurchaseOrderLine, pk=kwargs.get('pk'))
        po = line.purchase_order
        context = {
            'object': po,
            'lines': po.lines.all(),
            'line_form': PurchaseOrderLineForm(),
            'editing_line_id': line.pk,
        }
        return render(request, 'procurement/includes/purchaseorder_lines_container.html', context)

    def post(self, request, *args, **kwargs):
        line = get_object_or_404(PurchaseOrderLine, pk=kwargs.get('pk'))
        po = line.purchase_order

        if po.status != PurchaseOrder.STATUS_DRAFT:
            messages.error(request, "Line items can only be edited while the purchase order is in Draft status.")
            context = {
                'object': po,
                'lines': po.lines.all(),
                'line_form': PurchaseOrderLineForm(),
            }
            return render(request, 'procurement/includes/purchaseorder_lines_container.html', context)

        try:
            qty_ordered = int(request.POST.get('qty_ordered', 1))
            if qty_ordered < 1:
                raise ValueError("Quantity must be at least 1.")
            
            unit_price_raw = request.POST.get('unit_price')
            unit_price = None
            if unit_price_raw and unit_price_raw.strip():
                from decimal import Decimal
                unit_price = Decimal(unit_price_raw)
                if unit_price < 0:
                    raise ValueError("Price cannot be negative.")
            
            line.qty_ordered = qty_ordered
            line.unit_price = unit_price
            line.save()
            messages.success(request, "Line item updated successfully.")
        except Exception as e:
            messages.error(request, f"Failed to update line item: {e}")
            
        context = {
            'object': po,
            'lines': po.lines.all(),
            'line_form': PurchaseOrderLineForm(),
        }
        return render(request, 'procurement/includes/purchaseorder_lines_container.html', context)

class PurchaseOrderReceiveView(SimplePostView):
    queryset = PurchaseOrder.objects.all()
    permission_required = 'procurement.receive_purchaseorder'

    def perform_action(self, obj, request):
        return {'url': reverse('procurement:purchaseorder_receive_form', kwargs={'pk': obj.pk})}

    def _htmx_success_response(self, obj, result):
        response = HttpResponse(status=204)
        response['HX-Redirect'] = result['url']
        return response

    def get_success_redirect(self, obj, result):
        if result and 'url' in result:
            return redirect(result['url'])
        return super().get_success_redirect(obj, result)


class PurchaseOrderTransitionView(SimplePostView):
    def _htmx_success_response(self, obj, result):
        response = HttpResponse(status=204)
        response['HX-Redirect'] = obj.get_absolute_url()
        messages.success(self.request, result.get('message', 'Action completed successfully.'))
        return response


class PurchaseOrderApproveView(PurchaseOrderTransitionView):
    queryset = PurchaseOrder.objects.all()
    permission_required = 'procurement.approve_purchaseorder'

    def perform_action(self, obj, request):
        from .services import approve_purchase_order
        return approve_purchase_order(obj, user=request.user, request=request)


class PurchaseOrderOrderView(PurchaseOrderTransitionView):
    queryset = PurchaseOrder.objects.all()
    permission_required = 'procurement.change_purchaseorder'

    def perform_action(self, obj, request):
        from .services import order_purchase_order
        return order_purchase_order(obj, user=request.user, request=request)


class PurchaseOrderCancelView(PurchaseOrderTransitionView):
    queryset = PurchaseOrder.objects.all()
    permission_required = 'procurement.change_purchaseorder'

    def perform_action(self, obj, request):
        from .services import cancel_purchase_order
        return cancel_purchase_order(obj, user=request.user, request=request)


class PurchaseOrderReopenView(PurchaseOrderTransitionView):
    queryset = PurchaseOrder.objects.all()
    permission_required = 'procurement.change_purchaseorder'

    def perform_action(self, obj, request):
        from .services import reopen_purchase_order
        return reopen_purchase_order(obj, user=request.user, request=request)

class PurchaseOrderReceiveFormView(ObjectDetailView):
    queryset = PurchaseOrder.objects.all()
    template_name = 'procurement/purchaseorder_receive.html'
    permission_required = 'procurement.receive_purchaseorder'

    def get_permission_required(self):
        return (self.permission_required,)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        po = self.get_object()
        
        # Prepare initial data for Step 1 formset
        initial_data = []
        outstanding_lines = []
        for line in po.lines.all():
            if line.qty_outstanding > 0:
                initial_data.append({
                    'line_id': line.pk,
                    'qty_to_receive': line.qty_outstanding
                })
                outstanding_lines.append(line)
        
        from .forms import ReceiveLineFormSet
        formset = ReceiveLineFormSet(initial=initial_data)
        
        context['formset'] = formset
        context['lines_and_forms'] = list(zip(outstanding_lines, formset))
        context['step'] = '1'
        return context

    def post(self, request, *args, **kwargs):
        po = self.get_object()
        self.object = po
        step = request.POST.get('step', '1')
        from .forms import ReceiveLineFormSet, AssetProvisionForm, BaseAssetProvisionFormSet
        from django.forms import formset_factory
        
        if step == '2':
            # Submitting Step 2 (Asset Provisioning)
            line_quantities = request.session.get('receive_po_quantities', {})
            line_quantities = {int(k): int(v) for k, v in line_quantities.items()}
            
            # Count the total number of physical assets being received to initialize the formset correctly
            total_assets = 0
            for lid, qty in line_quantities.items():
                line = po.lines.get(pk=lid)
                if line.asset_type:
                    total_assets += qty
            
            DynamicAssetProvisionFormSet = formset_factory(
                AssetProvisionForm, 
                formset=BaseAssetProvisionFormSet, 
                extra=total_assets
            )
            formset = DynamicAssetProvisionFormSet(request.POST)
            
            if formset.is_valid():
                from .services import receive_purchase_order
                try:
                    asset_details = formset.cleaned_data
                    receive_purchase_order(po, line_quantities, asset_details)
                    # Clear session
                    if 'receive_po_quantities' in request.session:
                        del request.session['receive_po_quantities']
                    messages.success(request, "Stock received and assets provisioned successfully.")
                    return redirect(po.get_absolute_url())
                except Exception as e:
                    messages.error(request, f"Error receiving purchase order: {e}")
                    return redirect(po.get_absolute_url())
            else:
                # Render step 2 again with errors
                lines_info = []
                for form in formset:
                    try:
                        line_id = form['line_id'].value()
                        if line_id:
                            line = po.lines.get(pk=int(line_id))
                            lines_info.append(line)
                        else:
                            lines_info.append(None)
                    except Exception:
                        lines_info.append(None)
                
                context = self.get_context_data(object=po)
                context['formset'] = formset
                context['lines_info'] = list(zip(lines_info, formset))
                context['step'] = '2'
                return render(request, 'procurement/purchaseorder_receive_step2.html', context)
                
        else:
            # Submitting Step 1 (Quantities)
            formset = ReceiveLineFormSet(request.POST)
            if formset.is_valid():
                line_quantities = {}
                for form in formset:
                    line_quantities[form.cleaned_data['line_id']] = form.cleaned_data['qty_to_receive']
                
                # Check if any asset lines are being received
                has_assets = False
                step2_initial = []
                for lid, qty in line_quantities.items():
                    if qty > 0:
                        line = po.lines.get(pk=lid)
                        if line.asset_type:
                            has_assets = True
                            # Generate asset tag previews
                            from assets.models import Asset, AssetTagSequence
                            dummy_asset = Asset(tenant=po.tenant, asset_type=line.asset_type)
                            seq = AssetTagSequence.resolve_sequence_for_asset(dummy_asset)
                            for i in range(qty):
                                tag_preview = f"{seq.prefix}{seq.next_value + i:0{seq.zero_padding}d}"
                                step2_initial.append({
                                    'line_id': line.pk,
                                    'asset_tag': tag_preview,
                                    'name': str(line.asset_type)
                                })
                
                if has_assets:
                    # Save quantities to session for step 2
                    request.session['receive_po_quantities'] = line_quantities
                    
                    # Initialize step 2 formset
                    DynamicAssetProvisionFormSet = formset_factory(
                        AssetProvisionForm, 
                        formset=BaseAssetProvisionFormSet, 
                        extra=len(step2_initial)
                    )
                    step2_formset = DynamicAssetProvisionFormSet(initial=step2_initial)
                    
                    lines_info = []
                    for init_data in step2_initial:
                        line = po.lines.get(pk=init_data['line_id'])
                        lines_info.append(line)
                        
                    context = self.get_context_data(object=po)
                    context['formset'] = step2_formset
                    context['lines_info'] = list(zip(lines_info, step2_formset))
                    context['step'] = '2'
                    return render(request, 'procurement/purchaseorder_receive_step2.html', context)
                else:
                    # Call receiving service directly (only non-asset inventory items)
                    from .services import receive_purchase_order
                    try:
                        receive_purchase_order(po, line_quantities, asset_details=None)
                        messages.success(request, "Stock received successfully.")
                        return redirect(po.get_absolute_url())
                    except Exception as e:
                        messages.error(request, f"Error receiving purchase order: {e}")
                        return redirect(po.get_absolute_url())
            else:
                # Step 1 invalid, render again with errors
                outstanding_lines = []
                for form in formset:
                    try:
                        line_id = form['line_id'].value()
                        if line_id:
                            outstanding_lines.append(po.lines.get(pk=int(line_id)))
                        else:
                            outstanding_lines.append(None)
                    except Exception:
                        outstanding_lines.append(None)
                
                context = self.get_context_data(object=po)
                context['formset'] = formset
                context['lines_and_forms'] = list(zip(outstanding_lines, formset))
                context['step'] = '1'
                return render(request, 'procurement/purchaseorder_receive.html', context)

