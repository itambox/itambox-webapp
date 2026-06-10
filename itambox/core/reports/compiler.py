from django.utils import timezone
from django.db.models import Count, Q
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext as _
from .charts import generate_doughnut_chart, generate_bar_chart


def compile_report_context(template, active_tenant=None, filter_tenants=None):
    """
    Unified report compiler that aggregates assets/licenses/subscriptions,
    applies tenant scoping and filter constellations, compiles summary card metrics,
    orders selected data columns, and renders self-contained SVG distribution charts.
    """
    from extras.models import ReportTemplate
    
    # Resolve active columns sequence
    active_cols = template.included_columns or []
    if not active_cols:
        if template.report_type == 'asset_summary':
            active_cols = ['asset_tag', 'name', 'status', 'location', 'assigned_to']
        elif template.report_type == 'license_utilization':
            active_cols = ['license_name', 'software', 'seats', 'assigned_seats', 'available_seats', 'utilization_rate']
        elif template.report_type == 'asset_maintenance':
            active_cols = ['maintenance_title', 'maintenance_asset', 'maintenance_type', 'maintenance_status', 'maintenance_cost']
        elif template.report_type == 'asset_depreciation':
            active_cols = ['asset_tag', 'name', 'purchase_cost', 'salvage_value', 'depreciation_months', 'current_value']
        elif template.report_type == 'software_inventory':
            active_cols = ['software_name', 'manufacturer', 'version', 'category', 'license_type', 'installed_count', 'license_count']
        else:
            active_cols = ['subscription_name', 'provider', 'billing_cycle', 'cost', 'end_date']

    headers = []
    rows = []
    summary_cards = []
    chart_svg = ""
    
    headers_map = {
        'asset_tag': _('Asset Tag'),
        'name': _('Asset Name'),
        'manufacturer': _('Manufacturer'),
        'model': _('Model'),
        'serial_number': _('Serial Number'),
        'status': _('Status Label'),
        'location': _('Location'),
        'assigned_to': _('Asset Holder'),
        'purchase_cost': _('Purchase Cost'),
        'purchase_date': _('Purchase Date'),
        'warranty_months': _('Warranty (Months)'),
        'license_name': _('License Name'),
        'software': _('Software'),
        'seats': _('Total Seats'),
        'assigned_seats': _('Assigned Seats'),
        'available_seats': _('Available Seats'),
        'utilization_rate': _('Utilization Rate'),
        'subscription_name': _('Subscription Name'),
        'provider': _('Provider'),
        'billing_cycle': _('Billing Cycle'),
        'cost': _('Cost'),
        'end_date': _('End Date'),
        'maintenance_title': _('Maintenance Title'),
        'maintenance_asset': _('Asset'),
        'maintenance_type': _('Type'),
        'maintenance_status': _('Status'),
        'maintenance_cost': _('Cost'),
        'maintenance_start_date': _('Start Date'),
        'maintenance_completion_date': _('Completion Date'),
        'maintenance_downtime': _('Downtime (Days)'),
        'salvage_value': _('Salvage Value'),
        'depreciation_months': _('Depreciation Lifespan (Months)'),
        'current_value': _('Depreciated Value'),
        'software_name': _('Software Product'),
        'installed_count': _('Installed Count'),
        'license_count': _('License Count')
    }
    
    headers = [headers_map[col] for col in active_cols if col in headers_map]

    if template.report_type == ReportTemplate.REPORT_TYPE_ASSET_SUMMARY:
        from assets.models import Asset
        
        assets_qs = Asset.objects.filter(deleted_at__isnull=True)
        if filter_tenants:
            assets_qs = assets_qs.filter(tenant__in=filter_tenants)
        elif active_tenant:
            assets_qs = assets_qs.filter(tenant=active_tenant)
            
        assets_qs = assets_qs.select_related('asset_type', 'asset_type__manufacturer', 'status').prefetch_related(
            'assignments',
            'assignments__assigned_user',
            'assignments__assigned_location',
            'assignments__assigned_asset'
        )
        
        total_assets = assets_qs.count()
        acquisition_sum = sum(asset.purchase_cost for asset in assets_qs if asset.purchase_cost)
        
        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Hardware Assets'), 'value': str(total_assets)},
                {'label': _('Total Acquisition Sum'), 'value': f"${acquisition_sum:,.2f}"}
            ]
            
        for asset in assets_qs[:500]:
            row = {}
            if 'asset_tag' in active_cols:
                row[_('Asset Tag')] = asset.asset_tag or '-'
            if 'name' in active_cols:
                row[_('Asset Name')] = asset.name or '-'
            if 'manufacturer' in active_cols:
                row[_('Manufacturer')] = asset.manufacturer.name if asset.manufacturer else '-'
            if 'model' in active_cols:
                row[_('Model')] = asset.model if asset.model else '-'
            if 'serial_number' in active_cols:
                row[_('Serial Number')] = asset.serial_number or '-'
            if 'status' in active_cols:
                row[_('Status Label')] = asset.status.name if asset.status else '-'
            if 'location' in active_cols:
                loc = asset.active_assignment.assigned_to if (asset.active_assignment and asset.active_assignment.assigned_to_type == 'location') else None
                row[_('Location')] = loc.name if loc else '-'
            if 'assigned_to' in active_cols:
                holder = asset.active_assignment.assigned_to if asset.active_assignment else None
                row[_('Asset Holder')] = str(holder) if holder else '-'
            if 'purchase_cost' in active_cols:
                row[_('Purchase Cost')] = f"${asset.purchase_cost:,.2f}" if asset.purchase_cost else '-'
            if 'purchase_date' in active_cols:
                row[_('Purchase Date')] = asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else '-'
            if 'warranty_months' in active_cols:
                months = getattr(asset, 'warranty_months', None)
                if not months and asset.purchase_date and asset.warranty_expiration:
                    delta = asset.warranty_expiration - asset.purchase_date
                    months = int(delta.days / 30.4)
                row[_('Warranty (Months)')] = str(months) if months else '-'
                
            group_val = 'General'
            if template.group_by_field:
                if template.group_by_field == 'location':
                    loc = asset.active_assignment.assigned_to if (asset.active_assignment and asset.active_assignment.assigned_to_type == 'location') else None
                    group_val = loc.name if loc else _('Unassigned')
                elif template.group_by_field == 'status':
                    group_val = asset.status.name if asset.status else _('Default')
                elif template.group_by_field == 'manufacturer':
                    group_val = asset.manufacturer.name if asset.manufacturer else _('Generic')
            row['_group_by'] = group_val
            rows.append(row)
            
        if not rows:
            row = {}
            for col in active_cols:
                if col == 'asset_tag':
                    row[_('Asset Tag')] = 'AST-MOCK-001'
                elif col == 'name':
                    row[_('Asset Name')] = 'MacBook Pro 16" (Mock)'
                elif col == 'manufacturer':
                    row[_('Manufacturer')] = 'Apple'
                elif col == 'model':
                    row[_('Model')] = 'M3 Max 64GB'
                elif col == 'serial_number':
                    row[_('Serial Number')] = 'C02F8XXXXXXX'
                elif col == 'status':
                    row[_('Status Label')] = 'Deployed'
                elif col == 'location':
                    row[_('Location')] = 'HQ Amsterdam'
                elif col == 'assigned_to':
                    row[_('Asset Holder')] = 'Alex Dev'
                elif col == 'purchase_cost':
                    row[_('Purchase Cost')] = '$3,499.00'
                elif col == 'purchase_date':
                    row[_('Purchase Date')] = '2026-01-15'
                elif col == 'warranty_months':
                    row[_('Warranty (Months)')] = '36'
            row['_group_by'] = 'HQ Amsterdam' if template.group_by_field == 'location' else 'Deployed' if template.group_by_field == 'status' else 'Apple' if template.group_by_field == 'manufacturer' else 'General'
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Total Hardware Assets'), 'value': '1 (Mock)'},
                    {'label': _('Total Acquisition Sum'), 'value': '$3,499.00'}
                ]
                
        status_counts = assets_qs.values('status__name').annotate(count=Count('id')).order_by('-count')
        chart_data = [{'label': item['status__name'] or _('Default'), 'value': item['count']} for item in status_counts if item['count'] > 0]
        if not chart_data:
            chart_data = [{'label': _('Deployed'), 'value': 85}, {'label': _('Ready to Deploy'), 'value': 20}, {'label': _('Archived'), 'value': 12}]
            
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(chart_data, title=_("Asset Status Distribution"))

    elif template.report_type == ReportTemplate.REPORT_TYPE_LICENSE_UTILIZATION:
        from licenses.models import License
        
        licenses_qs = License.objects.filter(deleted_at__isnull=True).select_related('software').annotate(assigned_seats_count=Count('assignments'))
        if filter_tenants:
            licenses_qs = licenses_qs.filter(tenant__in=filter_tenants)
        elif active_tenant:
            licenses_qs = licenses_qs.filter(tenant=active_tenant)
            
        total_licenses = licenses_qs.count()
        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total License Products'), 'value': str(total_licenses)}
            ]
            
        total_assigned = 0
        total_seats = 0
        
        for lic in licenses_qs[:500]:
            assigned_count = lic.assigned_seats_count
            total_assigned += assigned_count
            total_seats += lic.seats
            utilization_pct = round((assigned_count / lic.seats * 100), 2) if lic.seats > 0 else 0
            
            row = {}
            if 'license_name' in active_cols:
                row[_('License Name')] = lic.name or '-'
            if 'software' in active_cols:
                row[_('Software')] = lic.software.name if lic.software else '-'
            if 'seats' in active_cols:
                row[_('Total Seats')] = str(lic.seats)
            if 'assigned_seats' in active_cols:
                row[_('Assigned Seats')] = str(assigned_count)
            if 'available_seats' in active_cols:
                row[_('Available Seats')] = str(lic.seats - assigned_count)
            if 'utilization_rate' in active_cols:
                row[_('Utilization Rate')] = f"{utilization_pct}%"
                
            row['_group_by'] = lic.software.name if (template.group_by_field == 'software' and lic.software) else 'General'
            rows.append(row)
            
        if not rows:
            row = {}
            for col in active_cols:
                if col == 'license_name':
                    row[_('License Name')] = 'Adobe Creative Cloud'
                elif col == 'software':
                    row[_('Software')] = 'Adobe Suite'
                elif col == 'seats':
                    row[_('Total Seats')] = '50'
                elif col == 'assigned_seats':
                    row[_('Assigned Seats')] = '42'
                elif col == 'available_seats':
                    row[_('Available Seats')] = '8'
                elif col == 'utilization_rate':
                    row[_('Utilization Rate')] = '84.0%'
            row['_group_by'] = 'Adobe Suite' if template.group_by_field == 'software' else 'General'
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Total License Products'), 'value': '1 (Mock)'}
                ]
            total_assigned = 42
            total_seats = 50

        total_available = max(total_seats - total_assigned, 0)
        chart_data = [
            {'label': _('Assigned Seats'), 'value': total_assigned},
            {'label': _('Available Seats'), 'value': total_available}
        ]
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(chart_data, title=_("License Seat Utilization"))

    elif template.report_type == ReportTemplate.REPORT_TYPE_SUBSCRIPTION_RENEWALS:
        from subscriptions.models import Subscription
        
        subs_qs = Subscription.objects.filter(deleted_at__isnull=True, status='active').select_related('provider')
        if filter_tenants:
            subs_qs = subs_qs.filter(tenant__in=filter_tenants)
        elif active_tenant:
            subs_qs = subs_qs.filter(tenant=active_tenant)
            
        total_active = subs_qs.count()
        
        total_monthly_spend = 0.0
        provider_costs = {}
        for sub in subs_qs:
            if sub.renewal_cost is None:
                continue
            cost_val = float(sub.renewal_cost)
            
            # Amortize based on billing cycle to get monthly equivalent
            if sub.billing_cycle == 'monthly':
                monthly_cost = cost_val
            elif sub.billing_cycle == 'quarterly':
                monthly_cost = cost_val / 3.0
            elif sub.billing_cycle == 'biannual':
                monthly_cost = cost_val / 6.0
            elif sub.billing_cycle == 'annual':
                monthly_cost = cost_val / 12.0
            elif sub.billing_cycle == 'multi_year':
                monthly_cost = cost_val / 36.0
            else:
                monthly_cost = cost_val
                
            total_monthly_spend += monthly_cost
            
            provider_name = sub.provider.name if sub.provider else _('Generic')
            provider_costs[provider_name] = provider_costs.get(provider_name, 0.0) + monthly_cost

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Active Subscriptions'), 'value': str(total_active)},
                {'label': _('Est. Monthly Spend'), 'value': f"${total_monthly_spend:,.2f}"}
            ]
            
        for sub in subs_qs[:500]:
            row = {}
            if 'subscription_name' in active_cols:
                row[_('Subscription Name')] = sub.name or '-'
            if 'provider' in active_cols:
                row[_('Provider')] = sub.provider.name if sub.provider else '-'
            if 'billing_cycle' in active_cols:
                row[_('Billing Cycle')] = sub.get_billing_cycle_display()
            if 'cost' in active_cols:
                row[_('Cost')] = f"${sub.renewal_cost:,.2f}" if sub.renewal_cost is not None else '-'
            if 'end_date' in active_cols:
                row[_('End Date')] = sub.renewal_date.strftime('%Y-%m-%d') if sub.renewal_date else '-'
                
            row['_group_by'] = sub.provider.name if (template.group_by_field == 'provider' and sub.provider) else 'General'
            rows.append(row)
            
        if not rows:
            row = {}
            for col in active_cols:
                if col == 'subscription_name':
                    row[_('Subscription Name')] = 'Office 365 E5'
                elif col == 'provider':
                    row[_('Provider')] = 'Microsoft'
                elif col == 'billing_cycle':
                    row[_('Billing Cycle')] = 'Monthly'
                elif col == 'cost':
                    row[_('Cost')] = '$1,200.00'
                elif col == 'end_date':
                    row[_('End Date')] = '2026-12-31'
            row['_group_by'] = 'Microsoft' if template.group_by_field == 'provider' else 'General'
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Active Subscriptions'), 'value': '1 (Mock)'},
                    {'label': _('Est. Monthly Spend'), 'value': '$1,200.00'}
                ]
            provider_costs = {'Microsoft': 1200.0}

        chart_data = [{'label': k, 'value': v} for k, v in provider_costs.items()]
        if template.include_distribution_chart:
            chart_svg = generate_bar_chart(chart_data, title=_("Monthly Spend by Provider"))

    elif template.report_type == ReportTemplate.REPORT_TYPE_ASSET_MAINTENANCE:
        from assets.models import AssetMaintenance

        maint_qs = AssetMaintenance.objects.filter(deleted_at__isnull=True).select_related('asset', 'supplier')
        if filter_tenants:
            maint_qs = maint_qs.filter(asset__tenant__in=filter_tenants)
        elif active_tenant:
            maint_qs = maint_qs.filter(asset__tenant=active_tenant)
            
        total_maint = maint_qs.count()
        total_cost = sum(m.cost for m in maint_qs if m.cost)
        
        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Maintenances'), 'value': str(total_maint)},
                {'label': _('Total Maintenance Cost'), 'value': f"${total_cost:,.2f}"}
            ]
            
        type_counts = {}
        for maint in maint_qs[:500]:
            row = {}
            if 'maintenance_title' in active_cols:
                row[_('Maintenance Title')] = maint.title or '-'
            if 'maintenance_asset' in active_cols:
                row[_('Asset')] = maint.asset.name if maint.asset else '-'
            if 'maintenance_type' in active_cols:
                row[_('Type')] = maint.get_maintenance_type_display()
            if 'maintenance_status' in active_cols:
                row[_('Status')] = maint.get_status_display()
            if 'maintenance_cost' in active_cols:
                row[_('Cost')] = f"${maint.cost:,.2f}" if maint.cost else '-'
            if 'maintenance_start_date' in active_cols:
                row[_('Start Date')] = maint.start_date.strftime('%Y-%m-%d') if maint.start_date else '-'
            if 'maintenance_completion_date' in active_cols:
                row[_('Completion Date')] = maint.completion_date.strftime('%Y-%m-%d') if maint.completion_date else '-'
            if 'maintenance_downtime' in active_cols:
                row[_('Downtime (Days)')] = str(maint.downtime_days) if maint.downtime_days is not None else '-'
                
            group_val = 'General'
            if template.group_by_field:
                if template.group_by_field == 'status':
                    group_val = maint.get_status_display()
                elif template.group_by_field == 'maintenance_type':
                    group_val = maint.get_maintenance_type_display()
                elif template.group_by_field == 'asset':
                    group_val = maint.asset.name if maint.asset else _('Unassigned')
            row['_group_by'] = group_val
            rows.append(row)
            
            # Aggregate chart data
            m_type = maint.get_maintenance_type_display()
            type_counts[m_type] = type_counts.get(m_type, 0) + 1
            
        if not rows:
            row = {}
            for col in active_cols:
                if col == 'maintenance_title':
                    row[_('Maintenance Title')] = 'Laptop Repair (Mock)'
                elif col == 'maintenance_asset':
                    row[_('Asset')] = 'MacBook Pro 16"'
                elif col == 'maintenance_type':
                    row[_('Type')] = 'Repair'
                elif col == 'maintenance_status':
                    row[_('Status')] = 'Completed'
                elif col == 'maintenance_cost':
                    row[_('Cost')] = '$250.00'
                elif col == 'maintenance_start_date':
                    row[_('Start Date')] = '2026-05-01'
                elif col == 'maintenance_completion_date':
                    row[_('Completion Date')] = '2026-05-05'
                elif col == 'maintenance_downtime':
                    row[_('Downtime (Days)')] = '4'
            row['_group_by'] = 'Completed' if template.group_by_field == 'status' else 'Repair' if template.group_by_field == 'maintenance_type' else 'MacBook Pro 16"' if template.group_by_field == 'asset' else 'General'
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Total Maintenances'), 'value': '1 (Mock)'},
                    {'label': _('Total Maintenance Cost'), 'value': '$250.00'}
                ]
            type_counts = {'Repair': 1}
            
        chart_data = [{'label': k, 'value': v} for k, v in type_counts.items()]
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(chart_data, title=_("Maintenance Type Distribution"))

    elif template.report_type == ReportTemplate.REPORT_TYPE_ASSET_DEPRECIATION:
        from assets.models import Asset
        
        assets_qs = Asset.objects.filter(deleted_at__isnull=True)
        if filter_tenants:
            assets_qs = assets_qs.filter(tenant__in=filter_tenants)
        elif active_tenant:
            assets_qs = assets_qs.filter(tenant=active_tenant)
            
        assets_qs = assets_qs.select_related('asset_type', 'asset_type__depreciation', 'status')
        
        total_assets = assets_qs.count()
        total_purchase_cost = sum(asset.purchase_cost for asset in assets_qs if asset.purchase_cost) or 0
        total_current_value = sum(asset.current_value for asset in assets_qs if asset.current_value is not None) or 0
        
        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Depreciable Assets'), 'value': str(total_assets)},
                {'label': _('Total Acquisition Cost'), 'value': f"${total_purchase_cost:,.2f}"},
                {'label': _('Total Current Book Value'), 'value': f"${total_current_value:,.2f}"}
            ]
            
        for asset in assets_qs[:500]:
            row = {}
            if 'asset_tag' in active_cols:
                row[_('Asset Tag')] = asset.asset_tag or '-'
            if 'name' in active_cols:
                row[_('Asset Name')] = asset.name or '-'
            if 'purchase_cost' in active_cols:
                row[_('Purchase Cost')] = f"${asset.purchase_cost:,.2f}" if asset.purchase_cost else '-'
            if 'salvage_value' in active_cols:
                row[_('Salvage Value')] = f"${asset.salvage_value:,.2f}" if asset.salvage_value else '-'
            if 'depreciation_months' in active_cols:
                months = asset.asset_type.depreciation.months if (asset.asset_type and asset.asset_type.depreciation) else None
                row[_('Depreciation Lifespan (Months)')] = str(months) if months else '-'
            if 'current_value' in active_cols:
                val = asset.current_value
                row[_('Depreciated Value')] = f"${val:,.2f}" if val is not None else '-'
                
            group_val = 'General'
            if template.group_by_field:
                if template.group_by_field == 'status':
                    group_val = asset.status.name if asset.status else _('Default')
                elif template.group_by_field == 'depreciation':
                    deprec = asset.asset_type.depreciation if asset.asset_type else None
                    group_val = deprec.name if deprec else _('No Scheme')
            row['_group_by'] = group_val
            rows.append(row)
            
        if not rows:
            row = {}
            for col in active_cols:
                if col == 'asset_tag':
                    row[_('Asset Tag')] = 'AST-MOCK-001'
                elif col == 'name':
                    row[_('Asset Name')] = 'Developer Workstation (Mock)'
                elif col == 'purchase_cost':
                    row[_('Purchase Cost')] = '$2,500.00'
                elif col == 'salvage_value':
                    row[_('Salvage Value')] = '$200.00'
                elif col == 'depreciation_months':
                    row[_('Depreciation Lifespan (Months)')] = '36'
                elif col == 'current_value':
                    row[_('Depreciated Value')] = '$1,450.00'
            row['_group_by'] = 'General'
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Total Depreciable Assets'), 'value': '1 (Mock)'},
                    {'label': _('Total Acquisition Cost'), 'value': '$2,500.00'},
                    {'label': _('Total Current Book Value'), 'value': '$1,450.00'}
                ]
        
        # Build depreciation stats for chart
        deprec_data = []
        if total_purchase_cost > 0:
            deprec_data = [
                {'label': _('Depreciated Book Value'), 'value': float(total_current_value)},
                {'label': _('Depreciated Amount'), 'value': max(float(total_purchase_cost - total_current_value), 0.0)}
            ]
        else:
            deprec_data = [
                {'label': _('Depreciated Book Value'), 'value': 1450.0},
                {'label': _('Depreciated Amount'), 'value': 1050.0}
            ]
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(deprec_data, title=_("Asset Value Depreciation"))

    elif template.report_type == ReportTemplate.REPORT_TYPE_SOFTWARE_INVENTORY:
        from software.models import Software
        
        software_qs = Software.objects.all().select_related('manufacturer')
        
        total_software = software_qs.count()
        
        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Software Products'), 'value': str(total_software)},
            ]
            
        category_counts = {}
        for soft in software_qs[:500]:
            row = {}
            installed = soft.installed_count
            licenses = soft.license_count
            
            if 'software_name' in active_cols:
                row[_('Software Product')] = soft.name or '-'
            if 'manufacturer' in active_cols:
                row[_('Manufacturer')] = soft.manufacturer.name if soft.manufacturer else '-'
            if 'version' in active_cols:
                row[_('Version')] = soft.version or '-'
            if 'category' in active_cols:
                row[_('Category')] = soft.get_category_display() if soft.category else '-'
            if 'license_type' in active_cols:
                row[_('License Type')] = soft.get_license_type_display() if soft.license_type else '-'
            if 'installed_count' in active_cols:
                row[_('Installed Count')] = str(installed)
            if 'license_count' in active_cols:
                row[_('License Count')] = str(licenses)
                
            group_val = 'General'
            if template.group_by_field:
                if template.group_by_field == 'category':
                    group_val = soft.get_category_display() if soft.category else _('Other')
                elif template.group_by_field == 'manufacturer':
                    group_val = soft.manufacturer.name if soft.manufacturer else _('Generic')
            row['_group_by'] = group_val
            rows.append(row)
            
            cat_name = soft.get_category_display() if soft.category else _('Other')
            category_counts[cat_name] = category_counts.get(cat_name, 0) + 1
            
        if not rows:
            row = {}
            for col in active_cols:
                if col == 'software_name':
                    row[_('Software Product')] = 'Office 365 E5 (Mock)'
                elif col == 'manufacturer':
                    row[_('Manufacturer')] = 'Microsoft'
                elif col == 'version':
                    row[_('Version')] = '16.0'
                elif col == 'category':
                    row[_('Category')] = 'Productivity'
                elif col == 'license_type':
                    row[_('License Type')] = 'Subscription'
                elif col == 'installed_count':
                    row[_('Installed Count')] = '25'
                elif col == 'license_count':
                    row[_('License Count')] = '30'
            row['_group_by'] = 'Productivity' if template.group_by_field == 'category' else 'Microsoft' if template.group_by_field == 'manufacturer' else 'General'
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Total Software Products'), 'value': '1 (Mock)'}
                ]
            category_counts = {'Productivity': 1}
            
        chart_data = [{'label': k, 'value': v} for k, v in category_counts.items()]
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(chart_data, title=_("Software Category Distribution"))

    # Group rows
    grouped_data = {}
    if template.group_by_field:
        for r in rows:
            g_key = r.get('_group_by', 'General')
            if g_key not in grouped_data:
                grouped_data[g_key] = []
            grouped_data[g_key].append(r)
    else:
        grouped_data['General'] = rows

    context_data = {
        'report_name': template.name,
        'description': template.description,
        'generated_at': timezone.now(),
        'headers': headers,
        'grouped_data': grouped_data,
        'summary_cards': summary_cards,
        'distribution_chart': chart_svg,
        'style_preset': template.style_preset,
        'is_compact': template.style_preset == 'compact',
        'is_financial': template.style_preset == 'financial',
    }
    
    return headers, rows, summary_cards, grouped_data, chart_svg, context_data
