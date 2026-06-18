from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView, ObjectCloneView,
)
from itambox.utils import get_paginate_count
from itambox.panels import Panel
from assets.tables import AssetTable

from ..models import Site
from ..forms import SiteForm, SiteFilterForm
from ..tables import SiteTable
from ..filters import SiteFilterSet
from assets.models import Asset
from django_tables2 import RequestConfig


class SiteListView(ObjectListView):
    queryset = Site.objects.select_related('region', 'group', 'tenant').prefetch_related('tags').annotate(
        location_count=Count('locations', distinct=True),
        asset_count=Count('locations__assets', distinct=True),
    )
    filterset = SiteFilterSet
    filterset_form = SiteFilterForm
    table = SiteTable
    action_buttons = ('add',)


class SiteDetailView(ObjectDetailView):
    queryset = Site.objects.select_related('region', 'group', 'tenant').prefetch_related(
        'locations', 'locations__tenant', 'tags'
    )

    layout = (
        ((Panel('info', 'Site Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = self.get_object()

        from ..tables import LocationTable
        locations_table = LocationTable(site.locations.all(), request=self.request)
        locations_table.configure(self.request)

        site_assets = Asset.objects.filter(location__site=site)
        assets_table = AssetTable(site_assets, request=self.request)
        assets_table.configure(self.request)

        related_objects_list = []
        location_count = site.locations.count()
        if location_count:
            related_objects_list.append({
                'label': 'Locations',
                'count': location_count,
                'url': f"{reverse('organization:location_list')}?site={site.slug}"
            })
        asset_count = site_assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?site={site.slug}"
            })

        context['locations_table'] = locations_table
        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context


class SiteEditView(ObjectEditView):
    queryset = Site.objects.all()
    model = Site
    model_form = SiteForm
    template_name = 'generic/object_edit.html'


class SiteCloneView(ObjectCloneView):
    model = Site
    model_form = SiteForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'organization:site_list'


class SiteDeleteView(ObjectDeleteView):
    queryset = Site.objects.all()
    model = Site
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:site_list')

    def post(self, request, *args, **kwargs):
        site = self.get_object()
        location_count = site.locations.count()
        asset_count = Asset.objects.filter(location__site=site).count()

        if location_count > 0 or asset_count > 0:
            related_object_details = []
            if location_count > 0:
                related_object_details.append(_("%(count)d location%(plural)s") % {
                    'count': location_count,
                    'plural': 's' if location_count != 1 else '',
                })
            if asset_count > 0:
                related_object_details.append(_("%(count)d asset%(plural)s") % {
                    'count': asset_count,
                    'plural': 's' if asset_count != 1 else '',
                })

            messages.error(
                request,
                _("Cannot delete site '%(name)s': It is associated with %(details)s.") % {
                    'name': site.name,
                    'details': ', '.join(str(d) for d in related_object_details),
                }
            )
            return redirect(site.get_absolute_url())

        return super().post(request, *args, **kwargs)


class SiteBulkEditView(ObjectBulkEditView):
    queryset = Site.objects.all()


class SiteBulkDeleteView(ObjectBulkDeleteView):
    queryset = Site.objects.all()
