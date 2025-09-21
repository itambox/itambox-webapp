from django.db.models import Count
from django_tables2 import RequestConfig
from assetbox.views.generic import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView
from assetbox.utils import get_paginate_count
from assetbox.panels import Panel
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import View
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
    queryset = Provider.objects.prefetch_related('tags').annotate(subscription_count=Count('subscriptions'))
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

        subscriptions_qs = provider.subscriptions.select_related('tenant', 'owner')
        context['subscriptions_table'] = tables.SubscriptionTable(subscriptions_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(
            context['subscriptions_table']
        )
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
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(
            context['assignments_table']
        )
        context['assignment_count'] = assignments_qs.count()
        return context


class SubscriptionEditView(ObjectEditView):
    queryset = Subscription.objects.all()
    model_form = forms.SubscriptionForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'subscriptions:subscription_list'


class SubscriptionDeleteView(ObjectDeleteView):
    queryset = Subscription.objects.all()
    default_return_url = 'subscriptions:subscription_list'


class SubscriptionBulkEditView(ObjectBulkEditView):
    queryset = Subscription.objects.all()


class SubscriptionBulkDeleteView(ObjectBulkDeleteView):
    queryset = Subscription.objects.all()


class ProviderBulkEditView(ObjectBulkEditView):
    queryset = Provider.objects.all()


class ProviderBulkDeleteView(ObjectBulkDeleteView):
    queryset = Provider.objects.all()


# =============================================================================
# Subscription Assignment Views
# =============================================================================

class SubscriptionAssignmentCreateView(LoginRequiredMixin, View):
    template_name = 'subscriptions/subscriptionassignments/subscriptionassignment_form.html'

    def get(self, request, *args, **kwargs):
        content_type_id = request.GET.get('content_type')
        object_id = request.GET.get('object_id')

        if not content_type_id or not object_id:
            return HttpResponseBadRequest("Missing content_type or object_id")

        content_type = get_object_or_404(ContentType, id=content_type_id)
        target_obj = get_object_or_404(content_type.model_class(), id=object_id)

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
        target_obj = get_object_or_404(content_type.model_class(), id=object_id)

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

