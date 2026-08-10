from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from django_tables2 import RequestConfig

from assets.forms.import_forms import AssetHolderBulkImportForm
from compliance.services import scope_custody_receipts
from itambox.panels import Panel
from itambox.utils import get_paginate_count
from itambox.views.generic import (
    ObjectBulkDeleteView,
    ObjectBulkEditView,
    ObjectDeleteView,
    ObjectDetailView,
    ObjectEditView,
    ObjectImportView,
    ObjectListView,
)

from ..filters import AssetHolderFilterSet
from ..forms import AssetHolderFilterForm, AssetHolderForm, ContactAssignmentForm
from ..models import AssetHolder, ContactAssignment
from ..tables import (
    AssetAssignmentTable,
    AssetHolderTable,
)


class AssetHolderListView(ObjectListView):
    queryset = (
        AssetHolder.objects.select_related("tenant")
        .prefetch_related("tags")
        .annotate(
            assignment_count=Count("asset_assignments", filter=Q(asset_assignments__is_active=True)),
        )
    )
    filterset = AssetHolderFilterSet
    filterset_form = AssetHolderFilterForm
    table = AssetHolderTable
    action_buttons = ("add",)


class AssetHolderDetailView(ObjectDetailView):
    queryset = AssetHolder.objects.select_related("tenant", "user").prefetch_related(
        "asset_assignments__asset", "asset_assignments__asset__status", "tags"
    )
    related_object_exclusions = ("compliance.custodyreceipt",)

    layout = (((Panel("info", _("Asset Holder Details")),),),)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assetholder = self.get_object()

        assignments_table = AssetAssignmentTable(assetholder.checked_out_assets, request=self.request)
        assignments_table.configure(self.request)
        context["assignments_table"] = assignments_table

        # Accessory Assignments
        from inventory.models import AccessoryAssignment
        from inventory.tables import AccessoryAssignmentTable

        acc_assign_qs = AccessoryAssignment.objects.filter(assigned_holder=assetholder)
        accessory_assignments_table = AccessoryAssignmentTable(acc_assign_qs, request=self.request)
        accessory_assignments_table.configure(self.request)
        context["accessory_assignments_table"] = accessory_assignments_table

        # Consumable Dispatches
        from inventory.models import ConsumableAssignment
        from inventory.tables import ConsumableAssignmentTable

        con_assign_qs = ConsumableAssignment.objects.filter(assigned_holder=assetholder)
        consumable_dispatches_table = ConsumableAssignmentTable(con_assign_qs, request=self.request)
        consumable_dispatches_table.configure(self.request)
        context["consumable_dispatches_table"] = consumable_dispatches_table

        # Software License Seats
        from licenses.models import LicenseSeatAssignment
        from licenses.tables import LicenseSeatAssignmentTable

        lic_assign_qs = LicenseSeatAssignment.objects.filter(assigned_holder=assetholder)
        license_assignments_table = LicenseSeatAssignmentTable(lic_assign_qs, request=self.request)
        license_assignments_table.configure(self.request)
        context["license_assignments_table"] = license_assignments_table

        # Custody receipts
        from compliance.models import CustodyReceipt
        from compliance.tables import CustodyReceiptTable

        can_view_receipts = self.request.user.has_perm("compliance.view_custodyreceipt", obj=assetholder)
        context["can_view_custody_receipts"] = can_view_receipts
        custody_receipts_qs = CustodyReceipt.objects.none()
        if can_view_receipts:
            custody_receipts_qs = scope_custody_receipts(
                CustodyReceipt.objects.filter(
                    holder=assetholder,
                    asset__tenant=assetholder.tenant,
                ).select_related("asset", "holder", "custody_template"),
                user=self.request.user,
                permission="compliance.view_custodyreceipt",
            )
            custody_receipts_table = CustodyReceiptTable(custody_receipts_qs, request=self.request)
            custody_receipts_table.configure(self.request)
            context["custody_receipts_table"] = custody_receipts_table

        related_objects_list = []
        asset_count = assetholder.checked_out_assets.count()
        if asset_count:
            related_objects_list.append(
                {
                    "label": _("Assets"),
                    "count": asset_count,
                    "url": f"{reverse('assets:asset_list')}?holder={assetholder.pk}",
                }
            )
        accessory_count = acc_assign_qs.count()
        if accessory_count:
            related_objects_list.append(
                {
                    "label": _("Accessories"),
                    "count": accessory_count,
                    "url": f"{reverse('inventory:accessoryassignment_list')}?assigned_holder={assetholder.pk}",
                }
            )
        consumable_count = con_assign_qs.count()
        if consumable_count:
            related_objects_list.append(
                {
                    "label": _("Consumables"),
                    "count": consumable_count,
                    "url": f"{reverse('inventory:consumableassignment_list')}?assigned_holder={assetholder.pk}",
                }
            )
        license_count = lic_assign_qs.count()
        if license_count:
            related_objects_list.append(
                {
                    "label": _("Licenses"),
                    "count": license_count,
                    "url": f"{reverse('organization:assetholder_detail', kwargs={'pk': assetholder.pk})}#licenses",
                }
            )
        custody_count = custody_receipts_qs.count()
        if custody_count:
            related_objects_list.append(
                {
                    "label": _("Custody Receipts"),
                    "count": custody_count,
                    "url": f"{reverse('compliance:custodyreceipt_list')}?holder={assetholder.pk}",
                }
            )
        context["related_objects_list"] = related_objects_list

        return context


class AssetHolderEditView(ObjectEditView):
    queryset = AssetHolder.objects.all()
    model = AssetHolder
    model_form = AssetHolderForm
    template_name = "generic/object_edit.html"


class AssetHolderDeleteView(ObjectDeleteView):
    queryset = AssetHolder.objects.all()
    model = AssetHolder
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("organization:assetholder_list")

    def post(self, request, *args, **kwargs):
        assetholder = self.get_object()
        assignment_count = assetholder.asset_assignments.filter(is_active=True).count()

        if assignment_count > 0:
            messages.error(
                request,
                _("Cannot delete asset holder '%(holder)s': It has %(count)d active assignment%(plural)s.")
                % {
                    "holder": assetholder,
                    "count": assignment_count,
                    "plural": "s" if assignment_count != 1 else "",
                },
            )
            return redirect(assetholder.get_absolute_url())

        return super().post(request, *args, **kwargs)


class AssetHolderBulkEditView(ObjectBulkEditView):
    queryset = AssetHolder.objects.all()


class AssetHolderBulkDeleteView(ObjectBulkDeleteView):
    queryset = AssetHolder.objects.all()
