# *** INDENTATION FIX END ***

# --- AssetHolderAssignment Views ---

# Keep the function-based view for now unless refactoring is desired later
# @login_required
# def assetholderassignment_list(request):
#     # Corrected select_related fields and removed invalid prefetch_related for GFK
#     queryset = AssetHolderAssignment.objects.select_related('asset_holder', 'content_type')
#     # TODO: Add FilterSet and FilterForm if filtering is needed
#     # filterset = AssetHolderAssignmentFilterSet(request.GET, queryset=queryset)
#     # queryset = filterset.qs
#     table = AssetHolderAssignmentTable(queryset, request=request)
#     RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
#     model = AssetHolderAssignment
#     model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"
#     table_config_key = f"{model._meta.app_label}.{table.__class__.__name__}"
#     context = {
#         'table': table, 'title': 'Asset Holder Assignments', 'object_type': 'Asset Holder Assignment',
#         # 'create_url_name': 'organization:assetholderassignment_create', # No create view typically for assignments
#         'model_name_str': model_name_str, 'table_config_key': table_config_key,
#         # 'filter_form': filterset.form if filterset else None, # Uncomment if filterset is added
#     }
#     return render(request, 'generic/object_list.html', context)

class AssetHolderAssignmentListView(ObjectListView):
    queryset = AssetHolderAssignment.objects.select_related('asset_holder', 'content_type')
    # TODO: Add filterset and filterset_form if filtering becomes necessary
    # filterset = filters.AssetHolderAssignmentFilterSet 
    # filterset_form = forms.AssetHolderAssignmentFilterForm
    table = AssetHolderAssignmentTable # Use 'table' attribute for ObjectListView
    action_buttons = () # Read-only view

    # Add breadcrumbs
    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (reverse('organization:assetholder_list'), 'Asset Holders'), # Link to parent list
            (None, 'Assignments') # Current page
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Asset Holder Assignments' # Set specific title
        # Base class handles table, filter_form, model_name_str, table_config_key etc.
        return context 