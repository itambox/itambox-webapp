from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied
from django.urls import reverse, reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin

from assets.models import AssetRequest, Asset
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
    if request_instance.status != AssetRequest.STATUS_PENDING:
        raise ValidationError("Only pending requests can be approved.")
    
    asset = kwargs.get('allocated_asset')
    if asset:
        if not asset.is_requestable:
            raise ValidationError(f"Allocated asset '{asset.name}' is not marked as requestable.")
        if asset.status.type != 'deployable':
            raise ValidationError("Allocated asset must be in a deployable status.")
        if request_instance.asset_type and asset.asset_type != request_instance.asset_type:
            raise ValidationError("Allocated asset does not match the requested asset type.")
        request_instance.asset = asset

    request_instance.status = AssetRequest.STATUS_APPROVED
    request_instance.responded_by = user
    request_instance.response_date = timezone.now()
    request_instance.response_notes = kwargs.get('response_notes', '')
    request_instance.save()
    return request_instance


@transaction.atomic
def deny_asset_request(request_instance, user, request=None, **kwargs):
    if request_instance.status != AssetRequest.STATUS_PENDING:
        raise ValidationError("Only pending requests can be denied.")

    request_instance.status = AssetRequest.STATUS_DENIED
    request_instance.responded_by = user
    request_instance.response_date = timezone.now()
    request_instance.response_notes = kwargs.get('response_notes', '')
    request_instance.save()
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
        qs = super().get_queryset()
        # Non-staff users can only view their own requests
        if not self.request.user.is_staff:
            return qs.filter(requester=self.request.user)
        return qs


class RequestDetailView(ObjectDetailView):
    queryset = AssetRequest.objects.select_related('requester', 'asset_type', 'asset', 'responded_by')
    template_name = 'assets/requests/assetrequest_detail.html'
    panels = [
        Panel('Request Information', ['requester', 'asset_type', 'asset', 'status', 'request_date']),
        Panel('Decision Metadata', ['responded_by', 'response_date', 'response_notes']),
        Panel('Requester Notes', ['notes'], position='right')
    ]


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
        return super().form_valid(form)

    def alter_obj(self, obj, request, url_args, url_kwargs):
        obj.requester = request.user
        return obj


class RequestApproveView(GenericTransactionView):
    queryset = AssetRequest.objects.filter(status=AssetRequest.STATUS_PENDING)
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
            'response_notes': form.cleaned_data.get('response_notes')
        }


class RequestDenyView(GenericTransactionView):
    queryset = AssetRequest.objects.filter(status=AssetRequest.STATUS_PENDING)
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
    queryset = AssetRequest.objects.all()

    def perform_action(self, obj, request):
        if obj.requester != request.user and not request.user.is_staff:
            raise PermissionDenied("You do not have permission to cancel this request.")
            
        if obj.status not in [AssetRequest.STATUS_PENDING, AssetRequest.STATUS_APPROVED]:
            raise ValidationError("Only pending or approved requests can be cancelled.")
            
        if obj.status == AssetRequest.STATUS_APPROVED and obj.asset and obj.asset.assignments.filter(is_active=True).exists():
            raise ValidationError("Cannot cancel a request that has already initiated active physical checkout.")

        obj.status = AssetRequest.STATUS_CANCELLED
        obj.save()
        return {'message': "Asset request cancelled successfully."}
