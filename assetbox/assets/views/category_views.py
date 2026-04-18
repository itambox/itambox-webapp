from django.urls import reverse, reverse_lazy
from django_tables2 import RequestConfig

from ..models import Category, AssetType
from .. import forms, tables, filters

from inventory.models import Accessory

from assetbox.utils import get_paginate_count
from assetbox.panels import Panel
from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectCloneView,
)


class CategoryListView(ObjectListView):
    queryset = Category.objects.prefetch_related('tags')
    filterset = filters.CategoryFilterSet
    filterset_form = forms.CategoryFilterForm
    table = tables.CategoryTable
    action_buttons = ("add",)


class CategoryDetailView(ObjectDetailView):
    queryset = Category.objects.all()

    layout = (
        ((Panel('info', 'Category Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        category = self.get_object()

        cat_asset_types = AssetType.objects.filter(category=category).select_related('manufacturer')
        asset_types_table = tables.AssetTypeTable(cat_asset_types, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(asset_types_table)
        context['asset_types_table'] = asset_types_table

        cat_accessories = Accessory.objects.filter(category=category).select_related('manufacturer')
        accessories_table = tables.AccessoryTable(cat_accessories, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(accessories_table)
        context['accessories_table'] = accessories_table

        # Query active custody templates (Policies) linked to this category
        from compliance.models import CustodyTemplate
        context['custody_templates'] = CustodyTemplate.objects.filter(
            category=category,
            is_active=True
        ).select_related('tenant', 'tenant_group')

        related_objects_list = []
        assettype_count = cat_asset_types.count()
        if assettype_count:
            related_objects_list.append({
                'label': 'Asset Types',
                'count': assettype_count,
                'url': f"{reverse('assets:assettype_list')}?category={category.slug}"
            })
        accessory_count = cat_accessories.count()
        if accessory_count:
            related_objects_list.append({
                'label': 'Accessories',
                'count': accessory_count,
                'url': f"{reverse('inventory:accessory_list')}?category={category.slug}"
            })
        context['related_objects_list'] = related_objects_list
        return context


class CategoryEditView(ObjectEditView):
    queryset = Category.objects.all()
    model = Category
    model_form = forms.CategoryForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:category_list"


class CategoryDeleteView(ObjectDeleteView):
    queryset = Category.objects.all()
    model = Category
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:category_list")


class CategoryCloneView(ObjectCloneView):
    model = Category
    model_form = forms.CategoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:category_list'
