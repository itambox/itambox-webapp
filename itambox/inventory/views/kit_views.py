from django.urls import reverse, reverse_lazy
from django.db.models.functions import Coalesce

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectCloneView,
)
from itambox.views.generic.service_views import GenericTransactionView
from itambox.panels import Panel

from ..models import Kit, KitItem, Accessory, Consumable
from .. import forms, tables, filters
from assets.models import Asset
from assets.services import checkout_kit
from django.db.models import Count


class KitListView(ObjectListView):
    queryset = Kit.objects.select_related('tenant').annotate(item_count=Count('items'))
    filterset = filters.KitFilterSet
    filterset_form = forms.KitFilterForm
    table = tables.KitTable
    action_buttons = ('add',)


class KitDetailView(ObjectDetailView):
    queryset = Kit.objects.all().prefetch_related('items__asset_type', 'items__accessory', 'items__license__software', 'items__consumable')
    template_name = 'assets/kits/kit_detail.html'

    layout = (
        ((Panel('info', 'Kit Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Check availability of each kit item
        items_with_availability = []
        all_available = True

        items = list(self.object.items.all())
        asset_type_ids = [i.asset_type_id for i in items if i.asset_type_id]
        accessory_ids = [i.accessory_id for i in items if i.accessory_id]
        license_ids = [i.license_id for i in items if i.license_id]
        consumable_ids = [i.consumable_id for i in items if i.consumable_id]

        # 1. Batch Asset Availability Count
        asset_counts = {}
        if asset_type_ids:
            from django.db.models import Count
            counts = Asset.objects.filter(
                asset_type_id__in=asset_type_ids,
                status__slug='available'
            ).values('asset_type_id').annotate(count=Count('id'))
            asset_counts = {c['asset_type_id']: c['count'] for c in counts}

        # 2. Batch Accessory Available Qty
        accessory_avail = {}
        if accessory_ids:
            from django.db.models import Sum, OuterRef, Subquery, IntegerField
            from ..models import AccessoryStock, AccessoryAssignment
            # Independent Subqueries per reverse relation: summing stocks and
            # assignments in one .annotate() would cross-join them and inflate
            # both totals (|stocks| x |assignments| fan-out).
            stock_sub = AccessoryStock.objects.filter(
                accessory=OuterRef('pk')
            ).order_by().values('accessory').annotate(total=Sum('qty')).values('total')
            undeducted_sub = AccessoryAssignment.objects.filter(
                accessory=OuterRef('pk'), from_location__isnull=True
            ).order_by().values('accessory').annotate(total=Sum('qty')).values('total')
            stocks = Accessory.objects.filter(id__in=accessory_ids).annotate(
                total_qty=Coalesce(Subquery(stock_sub, output_field=IntegerField()), 0),
                undeducted_qty=Coalesce(Subquery(undeducted_sub, output_field=IntegerField()), 0)
            ).values('id', 'total_qty', 'undeducted_qty')
            for s in stocks:
                accessory_avail[s['id']] = max(0, s['total_qty'] - s['undeducted_qty'])

        # 3. Batch License Available Seats
        license_avail = {}
        if license_ids:
            from django.db.models import Count
            from licenses.models import License
            licenses = License.objects.filter(id__in=license_ids).annotate(
                assigned_count=Count('assignments')
            ).values('id', 'seats', 'assigned_count')
            for l in licenses:
                license_avail[l['id']] = max(0, l['seats'] - l['assigned_count'])

        # 4. Batch Consumable Available Qty
        consumable_avail = {}
        if consumable_ids:
            from django.db.models import Sum, OuterRef, Subquery, IntegerField
            from ..models import ConsumableStock, ConsumableAssignment
            # Independent Subqueries per reverse relation (see accessory block):
            # avoids the stocks x consumptions cartesian-product double-count.
            stock_sub = ConsumableStock.objects.filter(
                consumable=OuterRef('pk')
            ).order_by().values('consumable').annotate(total=Sum('qty')).values('total')
            undeducted_sub = ConsumableAssignment.objects.filter(
                consumable=OuterRef('pk'), from_location__isnull=True
            ).order_by().values('consumable').annotate(total=Sum('qty')).values('total')
            stocks = Consumable.objects.filter(id__in=consumable_ids).annotate(
                total_qty=Coalesce(Subquery(stock_sub, output_field=IntegerField()), 0),
                undeducted_qty=Coalesce(Subquery(undeducted_sub, output_field=IntegerField()), 0)
            ).values('id', 'total_qty', 'undeducted_qty')
            for s in stocks:
                consumable_avail[s['id']] = max(0, s['total_qty'] - s['undeducted_qty'])

        for item in items:
            avail = 0
            if item.asset_type_id:
                avail = asset_counts.get(item.asset_type_id, 0)
                if avail < 1:
                    all_available = False
            elif item.accessory_id:
                avail = accessory_avail.get(item.accessory_id, 0)
                if avail < item.qty:
                    all_available = False
            elif item.license_id:
                avail = license_avail.get(item.license_id, 0)
                if avail < 1:
                    all_available = False
            elif item.consumable_id:
                avail = consumable_avail.get(item.consumable_id, 0)
                if avail < item.qty:
                    all_available = False

            items_with_availability.append({
                'item': item,
                'available_count': avail,
                'is_available': (avail >= (item.qty if (item.accessory or item.consumable) else 1))
            })

        context['items_with_availability'] = items_with_availability
        context['all_available'] = all_available
        return context


class KitEditView(ObjectEditView):
    queryset = Kit.objects.all()
    model = Kit
    model_form = forms.KitForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:kit_list'


class KitCloneView(KitEditView, ObjectCloneView):
    model = Kit


class KitDeleteView(ObjectDeleteView):
    queryset = Kit.objects.all()
    model = Kit
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('inventory:kit_list')


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
        return reverse('inventory:kit_list')


class KitItemDeleteView(ObjectDeleteView):
    queryset = KitItem.objects.all()
    model = KitItem
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse('inventory:kit_list')


class KitCheckoutView(GenericTransactionView):
    queryset = Kit.objects.all()
    model_form = forms.KitCheckoutForm
    service_callable = checkout_kit
    context_object_name = 'kit'
    template_name = 'inventory/includes/kit_checkout_modal.html'
    error_partial = 'inventory/includes/kit_checkout_modal.html#checkout-modal-form'
    success_message = "Kit checked out successfully."
    hx_trigger = "kitListUpdated"
    form_field_map = {
        'assigned_holder': 'holder',
        'assigned_location': 'location',
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs['instance']
        kwargs['kit'] = self.get_object()
        return kwargs
