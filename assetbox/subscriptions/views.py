from django.db.models import Count
from django_tables2 import RequestConfig
from core.views import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView
from core.utils import get_paginate_count
from core.panels import Panel
from .models import Provider, Subscription
from . import forms
from . import tables
from . import filters


# =============================================================================
# Provider Views
# =============================================================================

class ProviderListView(ObjectListView):
    queryset = Provider.objects.annotate(subscription_count=Count('subscriptions'))
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
    queryset = Subscription.objects.select_related('provider', 'tenant', 'owner')
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

        assignments_qs = subscription.assignments.select_related('assigned_by')
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
