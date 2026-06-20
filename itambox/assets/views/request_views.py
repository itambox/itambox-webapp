from django.db import transaction
from django.shortcuts import render, redirect
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied, ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _
from django.urls import reverse, reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views import View


from assets.models import AssetRequest, Asset, StatusLabel
from assets.choices import RequestStatusChoices
from assets.forms.request_forms import AssetRequestForm, AssetRequestActionForm
from assets import filters, tables

from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView


# --- Service Layer Callables defined inside the View boundaries ---

@transaction.atomic
def approve_asset_request(request_instance, user, request=None, **kwargs):
    request_instance = AssetRequest.objects.select_for_update().get(pk=request_instance.pk)
    if request_instance.status != RequestStatusChoices.PENDING:
        raise ValidationError(_("Only pending requests can be approved."))
    
    asset = kwargs.get('allocated_asset')
    if asset:
        if not asset.is_requestable:
            raise ValidationError(_("Allocated asset '%(name)s' is not marked as requestable.") % {"name": asset.name})
        if asset.status.type != 'deployable':
            raise ValidationError(_("Allocated asset must be in a deployable status."))
        if request_instance.asset_type and asset.asset_type != request_instance.asset_type:
            raise ValidationError(_("Allocated asset does not match the requested asset type."))
        request_instance.asset = asset

    qty = kwargs.get('qty')
    if request_instance.component or request_instance.accessory or request_instance.consumable:
        if qty is not None:
            if qty <= 0:
                raise ValidationError(_("Quantity must be greater than zero."))
            if qty > request_instance.qty:
                raise ValidationError(_("Quantity cannot exceed requested quantity."))
            request_instance.qty = qty

    allocated_location = kwargs.get('allocated_location')
    if allocated_location:
        request_instance.source_location = allocated_location

    request_instance.status = RequestStatusChoices.APPROVED
    request_instance.responded_by = user
    request_instance.response_date = timezone.now()
    request_instance.response_notes = kwargs.get('response_notes', '')
    request_instance.save()
    
    if request_instance.is_group:
        for child in request_instance.sub_requests.filter(status=RequestStatusChoices.PENDING):
            child.status = RequestStatusChoices.APPROVED
            child.responded_by = user
            child.response_date = timezone.now()
            child.response_notes = request_instance.response_notes
            if allocated_location:
                child.source_location = allocated_location
            child.save()
            
    return request_instance


@transaction.atomic
def deny_asset_request(request_instance, user, request=None, **kwargs):
    request_instance = AssetRequest.objects.select_for_update().get(pk=request_instance.pk)
    if request_instance.status != RequestStatusChoices.PENDING:
        raise ValidationError(_("Only pending requests can be denied."))

    request_instance.status = RequestStatusChoices.DENIED
    request_instance.responded_by = user
    request_instance.response_date = timezone.now()
    request_instance.response_notes = kwargs.get('response_notes', '')
    request_instance.save()
    
    if request_instance.is_group:
        for child in request_instance.sub_requests.filter(status=RequestStatusChoices.PENDING):
            child.status = RequestStatusChoices.DENIED
            child.responded_by = user
            child.response_date = timezone.now()
            child.response_notes = request_instance.response_notes
            child.save()
            
    return request_instance


# --- Requisition Views ---

class RequestListView(ObjectListView):
    queryset = AssetRequest.objects.select_related('requester', 'asset_type', 'asset', 'responded_by')
    filterset = filters.AssetRequestFilterSet
    filterset_form = None
    table = tables.AssetRequestTable
    template_name = 'assets/requests/assetrequest_list.html'
    action_buttons = ('add',)

    def get_queryset(self):
        qs = super().get_queryset().filter(parent__isnull=True)
        # Non-privileged users can only view their own requests
        if not self.request.user.is_staff and not self.request.user.has_perm('assets.approve_assetrequest') and not self.request.user.has_perm('assets.fulfill_assetrequest'):
            return qs.filter(requester=self.request.user)
        return qs


class RequestDetailView(ObjectDetailView):
    queryset = AssetRequest.objects.select_related('requester', 'asset_type', 'asset', 'responded_by')
    template_name = 'assets/requests/assetrequest_detail.html'
    panels = [
        Panel('Asset Request Details', ['requester', 'asset_type', 'asset', 'status', 'request_date']),
        Panel('Decision & Response Details', ['responded_by', 'response_date', 'response_notes']),
        Panel('Requester Notes', ['notes'], position='right')
    ]

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.is_staff and not self.request.user.has_perm('assets.approve_assetrequest') and not self.request.user.has_perm('assets.fulfill_assetrequest'):
            return qs.filter(requester=self.request.user)
        return qs


class RequestCreateView(ObjectEditView):
    queryset = AssetRequest.objects.all()
    model_form = AssetRequestForm
    template_name = 'assets/requests/assetrequest_form.html'
    default_return_url = 'assets:request_list'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def form_valid(self, form):
        form.instance.requester = self.request.user
        qty = form.cleaned_data.get('qty') or 1
        asset_type = form.instance.asset_type
        if asset_type and qty > 1:
            # Create the parent group request
            form.instance.qty = qty
            form.instance.is_group = True
            response = super().form_valid(form)
            
            # Create the individual child requests
            for _i in range(qty):
                req = AssetRequest(
                    tenant=form.instance.tenant,
                    requester=self.request.user,
                    asset_type=asset_type,
                    qty=1,
                    parent=form.instance,
                    assigned_user=form.instance.assigned_user,
                    assigned_location=form.instance.assigned_location,
                    assigned_asset=form.instance.assigned_asset,
                    notes=form.instance.notes
                )
                req._skip_duplicate_check = True
                req.save()
            return response
        else:
            return super().form_valid(form)

    def alter_obj(self, obj, request, url_args, url_kwargs):
        obj.requester = request.user
        return obj


class RequestApproveView(GenericTransactionView):
    permission_required = 'assets.approve_assetrequest'
    queryset = AssetRequest.objects.filter(status=RequestStatusChoices.PENDING)
    model_form = AssetRequestActionForm
    template_name = 'assets/requests/assetrequest_approve.html'
    service_callable = approve_asset_request
    success_message = "Asset request approved successfully."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request_instance'] = self.get_object()
        return kwargs

    def get_service_kwargs(self, form):
        return {
            'allocated_asset': form.cleaned_data.get('allocated_asset'),
            'allocated_location': form.cleaned_data.get('allocated_location'),
            'qty': form.cleaned_data.get('qty'),
            'response_notes': form.cleaned_data.get('response_notes')
        }


class RequestDenyView(GenericTransactionView):
    permission_required = 'assets.approve_assetrequest'
    queryset = AssetRequest.objects.filter(status=RequestStatusChoices.PENDING)
    model_form = AssetRequestActionForm
    template_name = 'assets/requests/assetrequest_deny.html'
    service_callable = deny_asset_request
    success_message = "Asset request denied."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request_instance'] = self.get_object()
        return kwargs

    def get_service_kwargs(self, form):
        return {
            'response_notes': form.cleaned_data.get('response_notes')
        }


class RequestCancelView(SimplePostView):
    # Self-authorizing: the requester may cancel their own request; everyone else
    # needs staff/approve_assetrequest. The per-object ownership check lives in
    # perform_action, so opt out of the static permission gate (fail-closed base).
    permission_required = ()
    queryset = AssetRequest.objects.all()

    def perform_action(self, obj, request):
        if obj.requester != request.user and not (request.user.is_staff or request.user.has_perm('assets.approve_assetrequest')):
            raise PermissionDenied(_("You do not have permission to cancel this request."))
            
        with transaction.atomic():
            obj = AssetRequest.objects.select_for_update().get(pk=obj.pk)
            if obj.status not in [RequestStatusChoices.PENDING, RequestStatusChoices.APPROVED, RequestStatusChoices.PROCUREMENT]:
                raise ValidationError(_("Only pending, approved, or procurement requests can be cancelled."))

            if obj.status == RequestStatusChoices.APPROVED and obj.asset and obj.asset.assignments.filter(is_active=True).exists():
                raise ValidationError(_("Cannot cancel a request that has already initiated active physical checkout."))

            obj.status = RequestStatusChoices.CANCELLED
            obj.save()
            
            if obj.is_group:
                for child in obj.sub_requests.exclude(status__in=[RequestStatusChoices.CANCELLED, RequestStatusChoices.FULFILLED]):
                    child.status = RequestStatusChoices.CANCELLED
                    child.save()
                    
        return {'message': _("Asset request cancelled successfully.")}


class RequestClaimView(SimplePostView):
    # Self-authorizing: the requester / assigned user may claim; everyone else
    # needs staff/fulfill_assetrequest. Ownership check is in perform_action, so
    # opt out of the static permission gate (fail-closed base).
    permission_required = ()
    queryset = AssetRequest.objects.all()

    def perform_action(self, obj, request):
        is_requester = obj.requester_id == request.user.id
        is_assigned_user = obj.assigned_user and obj.assigned_user.user_id == request.user.id
        if not is_requester and not is_assigned_user and not (request.user.is_staff or request.user.has_perm('assets.fulfill_assetrequest')):
            raise PermissionDenied(_("You do not have permission to claim this asset."))

        with transaction.atomic():
            obj = AssetRequest.objects.select_for_update().get(pk=obj.pk)
            
            if obj.status != RequestStatusChoices.APPROVED:
                raise ValidationError(_("Only approved requests can be claimed."))

            requests_to_claim = [obj] if not obj.is_group else list(obj.sub_requests.all())
            
            # First validate all have assets
            for req in requests_to_claim:
                is_inventory = req.component or req.accessory or req.consumable
                if not is_inventory and not req.asset:
                    raise ValidationError(_("No asset has been allocated to this request."))

            from assets.services import checkout_asset
            
            for req in requests_to_claim:
                holder = req.assigned_user
                location = req.assigned_location
                asset_target = req.assigned_asset

                if not holder and not location and not asset_target:
                    holder = req.requester.asset_holder_profiles.filter(tenant=req.tenant).first()
                    if not holder:
                        raise ValidationError(_("Requester does not have an active Asset Holder profile to assign the asset to."))

                if req.component:
                    from inventory.models import ComponentAllocation
                    ComponentAllocation.objects.create(
                        component=req.component,
                        qty=req.qty,
                        assigned_holder=holder,
                        assigned_location=location,
                        assigned_asset=asset_target,
                        from_location=req.source_location,
                        notes=f"Self-service claim for approved Request #{req.pk}"
                    )
                elif req.accessory:
                    from inventory.models import AccessoryAssignment
                    AccessoryAssignment.objects.create(
                        accessory=req.accessory,
                        qty=req.qty,
                        assigned_holder=holder,
                        assigned_location=location,
                        assigned_asset=asset_target,
                        from_location=req.source_location,
                        notes=f"Self-service claim for approved Request #{req.pk}"
                    )
                elif req.consumable:
                    from inventory.models import ConsumableAssignment
                    ConsumableAssignment.objects.create(
                        consumable=req.consumable,
                        qty=req.qty,
                        assigned_holder=holder,
                        assigned_location=location,
                        assigned_asset=asset_target,
                        from_location=req.source_location,
                        notes=f"Self-service claim for approved Request #{req.pk}"
                    )
                else:
                    checkout_asset(
                        asset=req.asset,
                        holder=holder,
                        location=location,
                        asset_target=asset_target,
                        user=request.user,
                        request=request,
                        notes=f"Self-service claim for approved Request #{req.pk}"
                    )
                
                req.status = RequestStatusChoices.FULFILLED
                req.response_date = timezone.now()
                req.responded_by = request.user
                req.save(update_fields=['status', 'response_date', 'responded_by'])
                
            if obj.is_group:
                obj.status = RequestStatusChoices.FULFILLED
                obj.response_date = timezone.now()
                obj.responded_by = request.user
                obj.save(update_fields=['status', 'response_date', 'responded_by'])

        return {'message': _("Item(s) claimed successfully.")}


class RequestMarkFulfilledView(SimplePostView):
    # Self-authorizing: requires staff/fulfill_assetrequest, checked in
    # perform_action. Opt out of the static permission gate (fail-closed base).
    permission_required = ()
    queryset = AssetRequest.objects.all()

    def perform_action(self, obj, request):
        if not (request.user.is_staff or request.user.has_perm('assets.fulfill_assetrequest')):
            raise PermissionDenied(_("You do not have permission to mark this request as fulfilled."))

        with transaction.atomic():
            obj = AssetRequest.objects.select_for_update().get(pk=obj.pk)
            
            if obj.status != RequestStatusChoices.APPROVED:
                raise ValidationError(_("Only approved requests can be marked fulfilled."))

            requests_to_mark = [obj] if not obj.is_group else list(obj.sub_requests.all())
            
            for req in requests_to_mark:
                is_inventory = req.component or req.accessory or req.consumable
                if not is_inventory and not req.asset:
                    raise ValidationError(_("No asset has been allocated to this request."))

                req.status = RequestStatusChoices.FULFILLED
                req.response_date = timezone.now()
                req.responded_by = request.user
                req.save(update_fields=['status', 'response_date', 'responded_by'])
                
            if obj.is_group:
                obj.status = RequestStatusChoices.FULFILLED
                obj.response_date = timezone.now()
                obj.responded_by = request.user
                obj.save(update_fields=['status', 'response_date', 'responded_by'])

        return {'message': _("Request marked as fulfilled (no checkout generated).")}


class RequestBulkReceiveView(PermissionRequiredMixin, View):
    permission_required = 'assets.fulfill_assetrequest'

    def get(self, request, *args, **kwargs):
        return redirect('assets:request_list')

    def post(self, request, *args, **kwargs):
        from assets.forms.request_forms import AssetReceiveFormSet
        from django.contrib import messages

        if 'form-TOTAL_FORMS' in request.POST:
            formset = AssetReceiveFormSet(request.POST)
            if formset.is_valid():
                try:
                    with transaction.atomic():
                        for form in formset:
                            request_id = form.cleaned_data['request_id']
                            req = AssetRequest.objects.select_for_update().get(pk=request_id)
                            
                            # Create Asset using form details
                            asset = Asset.objects.create(
                                name=form.cleaned_data['name'].strip(),
                                asset_type=req.asset_type,
                                asset_role=req.asset_type.asset_role if req.asset_type else None,
                                serial_number=form.cleaned_data['serial_number'].strip() or '',
                                asset_tag=form.cleaned_data['asset_tag'].strip() or '',
                                status=form.cleaned_data['status'],
                                location=form.cleaned_data['location'],
                                supplier=form.cleaned_data['supplier'],
                                order_number=form.cleaned_data['order_number'].strip() or '',
                                purchase_cost=form.cleaned_data['purchase_cost'],
                                purchase_date=form.cleaned_data['purchase_date'] or timezone.now().date(),
                                tenant=req.tenant,
                            )
                            
                            req.asset = asset
                            req.status = RequestStatusChoices.FULFILLED
                            req.responded_by = request.user
                            req.response_date = timezone.now()
                            req.save()
                            
                        messages.success(request, _("Stock received and requests fulfilled successfully."))
                        return redirect('assets:request_list')
                except Exception as e:
                    messages.error(request, _("Error processing bulk receipt: %(error)s") % {"error": e})
            
            requests_data = []
            for form in formset:
                try:
                    req_id = form['request_id'].value()
                    req = AssetRequest.objects.get(pk=int(req_id))
                    requests_data.append((req, form))
                except Exception:
                    requests_data.append((None, form))
                    
            context = {
                'title': _('Bulk Stock Receipt & Allocation'),
                'formset': formset,
                'requests_data': requests_data,
            }
            return render(request, 'assets/requests/bulk_receive.html', context)
            
        else:
            pks = request.POST.getlist('pk')
            if not pks:
                pks = request.GET.getlist('pk')
            
            if not pks:
                messages.warning(request, _("No requests selected for bulk receipt."))
                return redirect('assets:request_list')
                
            requests_qs = AssetRequest.objects.filter(pk__in=pks, status=RequestStatusChoices.APPROVED).select_related('asset_type', 'requester')
            if not requests_qs.exists():
                messages.warning(request, _("None of the selected requests are in Approved status."))
                return redirect('assets:request_list')
                
            initial_data = []
            type_tag_seqs = {}
            
            for req in requests_qs:
                if req.asset_type:
                    dummy_asset = Asset(tenant=req.tenant, asset_type=req.asset_type)
                    from assets.models import AssetTagSequence
                    seq = AssetTagSequence.resolve_sequence_for_asset(dummy_asset)
                    
                    next_tag = ""
                    if seq:
                        if seq.pk not in type_tag_seqs:
                            type_tag_seqs[seq.pk] = (seq, seq.next_value)
                        
                        seq_obj, current_val = type_tag_seqs[seq.pk]
                        next_tag = f"{seq_obj.prefix}{current_val:0{seq_obj.zero_padding}d}"
                        type_tag_seqs[seq.pk] = (seq_obj, current_val + 1)
                        
                    deployable_status = StatusLabel.objects.filter(type='deployable').first()
                    
                    initial_row = {
                        'request_id': req.pk,
                        'asset_tag': next_tag,
                        'name': str(req.asset_type),
                        'status': deployable_status.pk if deployable_status else None,
                        'location': req.assigned_location.pk if req.assigned_location else (req.source_location.pk if req.source_location else None),
                    }
                    initial_data.append(initial_row)
                    
            formset = AssetReceiveFormSet(initial=initial_data)
            requests_data = list(zip(requests_qs, formset))
            
            context = {
                'title': _('Bulk Stock Receipt & Allocation'),
                'formset': formset,
                'requests_data': requests_data,
            }
            return render(request, 'assets/requests/bulk_receive.html', context)

