from django.db.models import Count, Q
from django_tables2 import RequestConfig
from itambox.views.generic import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView, ObjectCloneView
from itambox.utils import get_paginate_count
from itambox.panels import Panel
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, HttpResponseBadRequest
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic import View
import json
from django.contrib import messages
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from .models import Provider, Subscription, SubscriptionAssignment
from . import forms
from . import tables
from . import filters


# =============================================================================
# Provider Views
# =============================================================================

class ProviderListView(ObjectListView):
    queryset = Provider.objects.prefetch_related('tags').annotate(
        subscription_count=Count('subscriptions', filter=Q(subscriptions__deleted_at__isnull=True))
    )
    filterset = filters.ProviderFilterSet
    filterset_form = forms.ProviderFilterForm
    table = tables.ProviderTable
    action_buttons = ('add',)


class ProviderDetailView(ObjectDetailView):
    queryset = Provider.objects.prefetch_related('subscriptions', 'tags')
    template_name = 'subscriptions/provider_detail.html'

    layout = (
        ((Panel('info', 'Provider Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        provider = self.get_object()

        subscriptions_qs = provider.subscriptions.all()
        context['subscriptions_table'] = tables.SubscriptionTable(subscriptions_qs, request=self.request)
        context['subscriptions_table'].configure(self.request)
        context['subscription_count'] = subscriptions_qs.count()
        context['active_subscription_count'] = subscriptions_qs.filter(status='active').count()
        return context


class ProviderEditView(ObjectEditView):
    queryset = Provider.objects.all()
    model_form = forms.ProviderForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'subscriptions:provider_list'


class ProviderDeleteView(ObjectDeleteView):
    queryset = Provider.objects.all()
    default_return_url = 'subscriptions:provider_list'


class ProviderCloneView(ProviderEditView, ObjectCloneView):
    model = Provider


# =============================================================================
# Subscription Views
# =============================================================================

class SubscriptionListView(ObjectListView):
    queryset = Subscription.objects.select_related('provider', 'tenant', 'owner').prefetch_related('tags')
    filterset = filters.SubscriptionFilterSet
    filterset_form = forms.SubscriptionFilterForm
    table = tables.SubscriptionTable
    action_buttons = ('add',)


class SubscriptionDetailView(ObjectDetailView):
    queryset = Subscription.objects.select_related('provider', 'tenant', 'owner').prefetch_related('tags', 'assignments')
    template_name = 'subscriptions/subscription_detail.html'

    layout = (
        ((Panel('info', 'Subscription Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        subscription = self.get_object()

        # Prefetch generic foreign key content objects cleanly
        assignments_qs = subscription.assignments.select_related('assigned_by', 'content_type')
        
        # 1. Map content types to primary keys to batch load related models in 1 query per type
        gfk_mapping = {}
        for assignment in assignments_qs:
            gfk_mapping.setdefault(assignment.content_type, []).append(assignment.object_id)
            
        # Batch execute query scans per unique model class
        prefetch_cache = {}
        for content_type, object_ids in gfk_mapping.items():
            model_class = content_type.model_class()
            if model_class:
                prefetch_cache[content_type] = {
                    obj.pk: obj for obj in model_class.objects.filter(pk__in=object_ids)
                }
            
        # 2. Re-assign resolved target objects onto assignments in-memory
        for assignment in assignments_qs:
            assignment.assigned_object = prefetch_cache.get(
                assignment.content_type, {}
            ).get(assignment.object_id)

        context['assignments_table'] = tables.SubscriptionAssignmentTable(assignments_qs, request=self.request)
        context['assignments_table'].configure(self.request)
        # assignments_qs is already fully materialized by the loops above, so
        # len() reuses the result cache instead of issuing a second COUNT query.
        context['assignment_count'] = len(assignments_qs)
        return context


class SubscriptionEditView(ObjectEditView):
    queryset = Subscription.objects.all()
    model_form = forms.SubscriptionForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'subscriptions:subscription_list'


class SubscriptionDeleteView(ObjectDeleteView):
    queryset = Subscription.objects.all()
    default_return_url = 'subscriptions:subscription_list'


class SubscriptionCloneView(SubscriptionEditView, ObjectCloneView):
    model = Subscription


class SubscriptionBulkEditView(ObjectBulkEditView):
    queryset = Subscription.objects.all()


class SubscriptionBulkDeleteView(ObjectBulkDeleteView):
    queryset = Subscription.objects.all()


class SubscriptionRenewView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'subscriptions.change_subscription'
    template_name = 'subscriptions/includes/subscription_renew_modal.html'

    def get(self, request, pk, *args, **kwargs):
        subscription = get_object_or_404(Subscription.objects.all(), pk=pk)
        form = forms.SubscriptionRenewForm(subscription=subscription)
        return render(request, self.template_name, {
            'form': form,
            'subscription': subscription,
        })

    def post(self, request, pk, *args, **kwargs):
        subscription = get_object_or_404(Subscription.objects.all(), pk=pk)
        form = forms.SubscriptionRenewForm(request.POST, subscription=subscription)
        if form.is_valid():
            renewal_date = form.cleaned_data['renewal_date']
            renewal_cost = form.cleaned_data['renewal_cost']
            subscription.renew(renewal_date, renewal_cost)
            
            # Create Journal Entry
            from extras.models import JournalEntry
            obj_type = ContentType.objects.get_for_model(Subscription)
            JournalEntry.objects.create(
                model=obj_type,
                object_id=subscription.pk,
                user=request.user,
                comment=f"Renewed subscription. Next renewal date: {renewal_date}. Cost: {renewal_cost or '—'} {subscription.currency}."
            )

            messages.success(request, f"Subscription '{subscription.name}' renewed successfully.")
            if request.htmx:
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "tableRefreshRequired": None,
                    "showMessage": {
                        "message": f"Subscription '{subscription.name}' renewed successfully.",
                        "level": "success"
                    }
                })
                return response
            return redirect(subscription.get_absolute_url())

        return render(request, self.template_name, {
            'form': form,
            'subscription': subscription,
        })


class SubscriptionCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'subscriptions.change_subscription'
    template_name = 'subscriptions/includes/subscription_cancel_modal.html'

    def get(self, request, pk, *args, **kwargs):
        subscription = get_object_or_404(Subscription.objects.all(), pk=pk)
        form = forms.SubscriptionCancelForm()
        return render(request, self.template_name, {
            'form': form,
            'subscription': subscription,
        })

    def post(self, request, pk, *args, **kwargs):
        subscription = get_object_or_404(Subscription.objects.all(), pk=pk)
        form = forms.SubscriptionCancelForm(request.POST)
        if form.is_valid():
            cancellation_date = form.cleaned_data['cancellation_date']
            reason = form.cleaned_data['reason']
            subscription.cancel(cancellation_date, reason)
            
            # Create Journal Entry
            from extras.models import JournalEntry
            obj_type = ContentType.objects.get_for_model(Subscription)
            comment_text = f"Cancelled subscription. Cancellation Date: {cancellation_date}."
            if reason:
                comment_text += f" Reason: {reason}"
            JournalEntry.objects.create(
                model=obj_type,
                object_id=subscription.pk,
                user=request.user,
                comment=comment_text
            )

            messages.success(request, f"Subscription '{subscription.name}' cancelled successfully.")
            if request.htmx:
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "tableRefreshRequired": None,
                    "showMessage": {
                        "message": f"Subscription '{subscription.name}' cancelled successfully.",
                        "level": "success"
                    }
                })
                return response
            return redirect(subscription.get_absolute_url())

        return render(request, self.template_name, {
            'form': form,
            'subscription': subscription,
        })


class SubscriptionSuspendView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'subscriptions.change_subscription'

    def post(self, request, pk, *args, **kwargs):
        subscription = get_object_or_404(Subscription.objects.all(), pk=pk)
        subscription.suspend()

        # Create Journal Entry
        from extras.models import JournalEntry
        obj_type = ContentType.objects.get_for_model(Subscription)
        JournalEntry.objects.create(
            model=obj_type,
            object_id=subscription.pk,
            user=request.user,
            comment="Suspended subscription."
        )

        messages.success(request, f"Subscription '{subscription.name}' suspended successfully.")
        if request.htmx:
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "tableRefreshRequired": None,
                "showMessage": {
                    "message": f"Subscription '{subscription.name}' suspended successfully.",
                    "level": "success"
                }
            })
            return response
        return redirect(subscription.get_absolute_url())


class SubscriptionCheckoutView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'subscriptions.change_subscription'
    template_name = 'subscriptions/includes/subscription_checkout_modal.html'

    def get(self, request, pk, *args, **kwargs):
        subscription = get_object_or_404(Subscription.objects.all(), pk=pk)
        form = forms.SubscriptionCheckoutForm(subscription=subscription)
        return render(request, self.template_name, {
            'form': form,
            'subscription': subscription,
        })

    def post(self, request, pk, *args, **kwargs):
        subscription = get_object_or_404(Subscription.objects.all(), pk=pk)
        form = forms.SubscriptionCheckoutForm(request.POST, subscription=subscription)
        if form.is_valid():
            target_type = form.cleaned_data['target_type']
            notes = form.cleaned_data['notes']
            
            if target_type == 'holder':
                target_obj = form.cleaned_data['assigned_holder']
            elif target_type == 'asset':
                target_obj = form.cleaned_data['asset']
            else:
                target_obj = form.cleaned_data['location']
                
            content_type = ContentType.objects.get_for_model(target_obj)
            
            # Create SubscriptionAssignment
            assignment = SubscriptionAssignment.objects.create(
                subscription=subscription,
                content_type=content_type,
                object_id=target_obj.pk,
                assigned_by=request.user,
                notes=notes
            )
            
            messages.success(request, f"Assigned subscription '{subscription.name}' successfully to {target_obj}.")
            if request.htmx:
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "tableRefreshRequired": None,
                    "showMessage": {
                        "message": f"Assigned subscription '{subscription.name}' successfully to {target_obj}.",
                        "level": "success"
                    }
                })
                return response
            return redirect(subscription.get_absolute_url())

        return render(request, self.template_name, {
            'form': form,
            'subscription': subscription,
        })


class ProviderBulkEditView(ObjectBulkEditView):
    queryset = Provider.objects.all()


class ProviderBulkDeleteView(ObjectBulkDeleteView):
    queryset = Provider.objects.all()


# =============================================================================
# Subscription Assignment Views
# =============================================================================

class SubscriptionAssignmentCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'subscriptions.add_subscriptionassignment'
    template_name = 'subscriptions/subscriptionassignments/subscriptionassignment_form.html'

    def get(self, request, *args, **kwargs):
        content_type_id = request.GET.get('content_type')
        object_id = request.GET.get('object_id')

        if not content_type_id or not object_id:
            return HttpResponseBadRequest("Missing content_type or object_id")

        content_type = get_object_or_404(ContentType, id=content_type_id)
        target_obj = get_object_or_404(content_type.model_class().objects.all(), id=object_id)

        form = forms.SubscriptionAssignmentForm(content_type=content_type, object_id=object_id)
        context = {
            'form': form,
            'target_obj': target_obj,
            'content_type': content_type,
            'object_id': object_id,
            'title': f"Assign Subscription to {target_obj}",
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        content_type_id = request.POST.get('content_type') or request.GET.get('content_type')
        object_id = request.POST.get('object_id') or request.GET.get('object_id')

        if not content_type_id or not object_id:
            return HttpResponseBadRequest("Missing content_type or object_id")

        content_type = get_object_or_404(ContentType, id=content_type_id)
        target_obj = get_object_or_404(content_type.model_class().objects.all(), id=object_id)

        form = forms.SubscriptionAssignmentForm(request.POST, content_type=content_type, object_id=object_id)
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.assigned_by = request.user
            assignment.save()
            messages.success(request, f"Assigned subscription successfully to {target_obj}.")
            return redirect(target_obj.get_absolute_url())

        context = {
            'form': form,
            'target_obj': target_obj,
            'content_type': content_type,
            'object_id': object_id,
            'title': f"Assign Subscription to {target_obj}",
        }
        return render(request, self.template_name, context)


class SubscriptionAssignmentDeleteView(ObjectDeleteView):
    queryset = SubscriptionAssignment.objects.all()
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        return_url = self.request.GET.get('return_url') or self.request.POST.get('return_url')
        if return_url:
            return return_url
        obj = self.object
        if obj and obj.assigned_object and hasattr(obj.assigned_object, 'get_absolute_url'):
            return obj.assigned_object.get_absolute_url()
        return reverse('dashboard')

