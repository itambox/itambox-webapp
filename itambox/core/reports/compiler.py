from django.utils import timezone
from django.db.models import Count, Q, Sum
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext as _
from .charts import generate_doughnut_chart, generate_bar_chart


def _record_currency(record_currency, active_tenant):
    """Resolve a money record's currency: its own currency, else the active tenant's, else
    the configured default. Mirrors the subscription-renewals branch's _resolve_currency."""
    code = (record_currency or '').upper()
    if code:
        return code
    if active_tenant is not None and getattr(active_tenant, 'currency', None):
        return active_tenant.currency.upper()
    from django.conf import settings as _settings
    return (getattr(_settings, 'ITAMBOX_DEFAULT_CURRENCY', 'EUR') or 'EUR').upper()


def _format_per_currency(amount_by_currency):
    """Render a {currency: amount} mapping as ONE money figure per currency joined with
    ' · '. There is no FX source, so amounts in different currencies are NEVER summed into a
    single (meaningless) total. Mirrors the subscription-renewals spend card."""
    from extras.templatetags.money import money as _money_fmt
    from types import SimpleNamespace
    items = sorted(amount_by_currency.items(), key=lambda kv: kv[1], reverse=True)
    return ' · '.join(
        _money_fmt(amount, SimpleNamespace(currency=cur)) for cur, amount in items
    ) or _money_fmt(0, None)


def _money(amount, currency_value, active_tenant):
    """Render ONE money grid cell in the record's resolved currency (its own
    currency, else the active tenant's, else the configured default) via the
    money templatetag — never a hardcoded '$'. Returns '-' for a missing amount.
    Mirrors the per-currency summary-card path (_record_currency/_format_per_currency)."""
    if amount is None:
        return '-'
    from extras.templatetags.money import money as _money_fmt
    from types import SimpleNamespace
    code = _record_currency(currency_value, active_tenant)
    return _money_fmt(amount, SimpleNamespace(currency=code))


def compile_report_context(template, active_tenant=None, filter_tenants=None):
    """
    Unified report compiler that aggregates the domain models for a report_type,
    applies tenant scoping and filter constellations, compiles summary card metrics,
    orders selected data columns, and renders self-contained SVG distribution charts.

    Tenant scoping: each branch filters to filter_tenants (if set) else active_tenant.
    Models whose tenant FK is nullable but that are NOT shared catalogues (Asset,
    Subscription, License) are scoped STRICTLY (tenant == active_tenant) — a null-tenant
    row is treated as a system/non-tenant artifact and intentionally EXCLUDED from
    per-tenant reports (design decision 2026-06-22). Only genuine shared-catalogue
    models that opt in via allow_global_tenant (Software, inventory items) additionally
    include Q(tenant__isnull=True) so their global rows appear in every tenant's report.
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
            active_cols = ['maintenance_asset', 'maintenance_type', 'maintenance_status', 'maintenance_cost']
        elif template.report_type == 'asset_depreciation':
            active_cols = ['asset_tag', 'name', 'purchase_cost', 'salvage_value', 'depreciation_months', 'current_value']
        elif template.report_type == 'software_inventory':
            active_cols = ['software_name', 'manufacturer', 'version', 'category', 'license_type', 'installed_count', 'license_count']
        elif template.report_type == 'contract_renewals':
            active_cols = ['contract_number', 'contract_name', 'contract_type', 'contract_status', 'contract_supplier', 'contract_end_date', 'contract_days_until_expiry', 'contract_cost']
        elif template.report_type == 'warranty_expiration':
            active_cols = ['warranty_asset', 'warranty_type', 'warranty_provider', 'warranty_end_date', 'warranty_days_remaining', 'warranty_status']
        elif template.report_type == 'asset_disposal_eol':
            active_cols = ['disposal_asset', 'disposal_date', 'disposal_method', 'disposal_sanitization_method', 'disposal_weee_compliant', 'disposal_proceeds']
        elif template.report_type == 'hardware_inventory':
            active_cols = ['hw_item_type', 'hw_name', 'hw_manufacturer', 'hw_category', 'hw_total_stock', 'hw_available', 'hw_status']
        elif template.report_type == 'custody_compliance':
            active_cols = ['custody_asset', 'custody_holder', 'custody_status', 'custody_accepted_date', 'custody_eula_version', 'custody_signature_provider']
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
        'version': _('Version'),
        'category': _('Category'),
        'license_type': _('License Type'),
        'installed_count': _('Installed Count'),
        'contract_number': _('Contract #'),
        'contract_name': _('Contract Name'),
        'contract_type': _('Contract Type'),
        'contract_status': _('Contract Status'),
        'contract_supplier': _('Supplier'),
        'contract_start_date': _('Start Date'),
        'contract_end_date': _('End Date'),
        'contract_renewal_date': _('Renewal Date'),
        'contract_days_until_expiry': _('Days Until Expiry'),
        'contract_cost': _('Contract Cost'),
        'contract_billing_cycle': _('Billing Cycle'),
        'contract_auto_renew': _('Auto-Renew'),
        'contract_covered_assets': _('Covered Assets'),
        'contract_sla_response_time': _('SLA Response Time'),
        'contract_sla_resolution_time': _('SLA Resolution Time'),
        'contract_coverage_hours': _('Coverage Hours'),
        'warranty_asset': _('Asset'),
        'warranty_type': _('Warranty Type'),
        'warranty_provider': _('Provider'),
        'warranty_start_date': _('Start Date'),
        'warranty_end_date': _('End Date'),
        'warranty_days_remaining': _('Days Remaining'),
        'warranty_status': _('Status'),
        'warranty_cost': _('Warranty Cost'),
        'warranty_reference': _('Reference'),
        'disposal_asset': _('Asset'),
        'disposal_date': _('Disposal Date'),
        'disposal_method': _('Disposal Method'),
        'disposal_sanitization_method': _('Data Sanitization Method'),
        'disposal_sanitization_certificate': _('Sanitization Certificate'),
        'disposal_sanitized_by': _('Sanitized By'),
        'disposal_recipient': _('Recipient'),
        'disposal_proceeds': _('Proceeds'),
        'disposal_weee_compliant': _('WEEE Compliant'),
        'disposal_notes': _('Notes'),
        'hw_item_type': _('Item Type'),
        'hw_name': _('Name'),
        'hw_manufacturer': _('Manufacturer'),
        'hw_category': _('Category'),
        'hw_part_number': _('Part Number'),
        'hw_total_stock': _('Total Stock'),
        'hw_available': _('Available'),
        'hw_min_qty': _('Safety Threshold'),
        'hw_status': _('Stock Status'),
        'custody_asset': _('Asset'),
        'custody_holder': _('Holder'),
        'custody_status': _('Acceptance Status'),
        'custody_accepted_date': _('Accepted Date'),
        'custody_eula_version': _('EULA Version'),
        'custody_signature_provider': _('Signature Provider'),
        'custody_qms_reference': _('QMS Reference'),
        'custody_ip_address': _('IP Address'),
        'custody_created_date': _('Created Date'),
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
            'warranties',
            'assignments',
            'assignments__assigned_user',
            'assignments__assigned_location',
            'assignments__assigned_asset'
        )
        
        total_assets = assets_qs.count()
        # Aggregate in the DB rather than pulling every filtered asset (and its
        # prefetched assignments) into Python just to sum one column.
        acquisition_sum = assets_qs.aggregate(total=Sum('purchase_cost'))['total'] or 0
        # Bucket acquisition cost per currency (each Asset carries its own currency and there
        # is no FX source, so a single combined sum would be meaningless).
        acq_by_currency = {}
        for cur, total in (
            assets_qs.exclude(purchase_cost__isnull=True)
            .values('currency').annotate(c=Sum('purchase_cost')).values_list('currency', 'c')
        ):
            code = _record_currency(cur, active_tenant)
            acq_by_currency[code] = acq_by_currency.get(code, 0) + (total or 0)

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Hardware Assets'), 'value': str(total_assets)},
                {'label': _('Total Acquisition Sum'), 'value': _format_per_currency(acq_by_currency)}
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
                row[_('Purchase Cost')] = _money(asset.purchase_cost, getattr(asset, 'currency', None), active_tenant)
            if 'purchase_date' in active_cols:
                row[_('Purchase Date')] = asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else '-'
            if 'warranty_months' in active_cols:
                # Warranty is its own model now (Asset has no warranty_months field).
                # Compute the active warranty's term from the prefetched relation —
                # no N+1, no dead getattr. (A dedicated warranty report covers the rest.)
                import datetime as _dt
                _today = _dt.date.today()
                _active_warranty = next(
                    (w for w in asset.warranties.all()
                     if w.deleted_at is None and w.start_date and w.end_date
                     and w.start_date <= _today <= w.end_date),
                    None,
                )
                months = int((_active_warranty.end_date - _active_warranty.start_date).days / 30.4) if _active_warranty else None
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
        
        # Count only *active* seat assignments. A bare Count('assignments') also
        # tallies soft-deleted (checked-in) seats, overstating utilization and the
        # downstream SAM/financial figures.
        licenses_qs = License.objects.filter(deleted_at__isnull=True).select_related('software').annotate(
            assigned_seats_count=Count('assignments', filter=Q(assignments__deleted_at__isnull=True))
        )
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
        
        # 'tenant' is select_related because _resolve_currency() reads sub.tenant
        # for every blank-currency subscription — without it that is an N+1.
        subs_qs = Subscription.objects.filter(deleted_at__isnull=True, status='active').select_related('provider', 'tenant')
        if filter_tenants:
            subs_qs = subs_qs.filter(tenant__in=filter_tenants)
        elif active_tenant:
            subs_qs = subs_qs.filter(tenant=active_tenant)
            
        total_active = subs_qs.count()

        # Subscriptions can carry differing ISO currencies and there is no FX
        # source — never sum monthly spend into one combined number. Bucket the
        # amortized monthly equivalent by the subscription's currency (blank =>
        # the owning tenant's currency) and render one figure per currency.
        from extras.templatetags.money import money as _money_fmt

        def _resolve_currency(sub):
            from django.conf import settings as _settings
            if sub.currency:
                return sub.currency.upper()
            t = sub.tenant
            if t is not None and getattr(t, 'currency', None):
                return t.currency.upper()
            return (getattr(_settings, 'ITAMBOX_DEFAULT_CURRENCY', 'EUR') or 'EUR').upper()

        monthly_by_currency = {}
        # Provider breakdown is bucketed per (provider, currency): with no FX
        # source we must not sum a provider's spend across differing currencies
        # into one bar. Each (provider, currency) becomes its own bar instead.
        provider_costs = {}  # (provider_name, currency) -> monthly_cost
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
            elif sub.billing_cycle == 'onetime':
                # A one-time charge is not recurring — it contributes 0 to the
                # estimated MONTHLY spend (and to the per-provider monthly bars).
                monthly_cost = 0.0
            else:
                monthly_cost = cost_val

            cur = _resolve_currency(sub)
            monthly_by_currency[cur] = monthly_by_currency.get(cur, 0.0) + monthly_cost

            provider_name = sub.provider.name if sub.provider else _('Generic')
            provider_costs[(provider_name, cur)] = provider_costs.get((provider_name, cur), 0.0) + monthly_cost

        if template.include_summary_cards:
            # One figure per currency; no cross-currency combined total.
            from types import SimpleNamespace
            spend_value = ' · '.join(
                _money_fmt(amount, SimpleNamespace(currency=cur))
                for cur, amount in sorted(monthly_by_currency.items(), key=lambda kv: kv[1], reverse=True)
            ) or _money_fmt(0, None)
            summary_cards = [
                {'label': _('Active Subscriptions'), 'value': str(total_active)},
                {'label': _('Est. Monthly Spend'), 'value': spend_value}
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
                row[_('Cost')] = _money(sub.renewal_cost, getattr(sub, 'currency', None), active_tenant)
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
            from django.conf import settings as _settings
            _mock_currency = (getattr(_settings, 'ITAMBOX_DEFAULT_CURRENCY', 'EUR') or 'EUR').upper()
            provider_costs = {('Microsoft', _mock_currency): 1200.0}

        # Render one bar per (provider, currency). When more than one currency is
        # present, qualify each bar's label with its ISO code so amounts in
        # different currencies are never shown as one undifferentiated bar
        # (there is no FX source to combine them). With a single currency the
        # plain provider name is kept.
        currencies_in_play = {cur for (_provider, cur) in provider_costs.keys()}
        multi_currency = len(currencies_in_play) > 1
        chart_data = [
            {
                'label': f"{provider_name} ({cur})" if multi_currency else provider_name,
                'value': v,
                'display': _money(v, cur, active_tenant),
            }
            for (provider_name, cur), v in provider_costs.items()
        ]
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
        # Bucket maintenance cost per currency (AssetMaintenance carries its own currency).
        total_cost = 0
        cost_by_currency = {}
        for m in maint_qs:
            if m.cost:
                total_cost += m.cost
                code = _record_currency(getattr(m, 'currency', None), active_tenant)
                cost_by_currency[code] = cost_by_currency.get(code, 0) + m.cost

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Maintenances'), 'value': str(total_maint)},
                {'label': _('Total Maintenance Cost'), 'value': _format_per_currency(cost_by_currency)}
            ]
            
        type_counts = {}
        for maint in maint_qs[:500]:
            row = {}
            if 'maintenance_asset' in active_cols:
                row[_('Asset')] = maint.asset.name if maint.asset else '-'
            if 'maintenance_type' in active_cols:
                row[_('Type')] = maint.get_maintenance_type_display()
            if 'maintenance_status' in active_cols:
                row[_('Status')] = maint.get_status_display()
            if 'maintenance_cost' in active_cols:
                row[_('Cost')] = _money(maint.cost, getattr(maint, 'currency', None), active_tenant)
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
                if col == 'maintenance_asset':
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
        # Bucket acquisition cost and current book value per currency (no FX source).
        total_purchase_cost = 0
        total_current_value = 0
        pc_by_currency = {}
        cv_by_currency = {}
        for asset in assets_qs:
            code = _record_currency(getattr(asset, 'currency', None), active_tenant)
            if asset.purchase_cost:
                total_purchase_cost += asset.purchase_cost
                pc_by_currency[code] = pc_by_currency.get(code, 0) + asset.purchase_cost
            if asset.current_value is not None:
                total_current_value += asset.current_value
                cv_by_currency[code] = cv_by_currency.get(code, 0) + asset.current_value

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Depreciable Assets'), 'value': str(total_assets)},
                {'label': _('Total Acquisition Cost'), 'value': _format_per_currency(pc_by_currency)},
                {'label': _('Total Current Book Value'), 'value': _format_per_currency(cv_by_currency)}
            ]
            
        for asset in assets_qs[:500]:
            row = {}
            if 'asset_tag' in active_cols:
                row[_('Asset Tag')] = asset.asset_tag or '-'
            if 'name' in active_cols:
                row[_('Asset Name')] = asset.name or '-'
            if 'purchase_cost' in active_cols:
                row[_('Purchase Cost')] = _money(asset.purchase_cost, getattr(asset, 'currency', None), active_tenant)
            if 'salvage_value' in active_cols:
                row[_('Salvage Value')] = _money(asset.salvage_value, getattr(asset, 'currency', None), active_tenant)
            if 'depreciation_months' in active_cols:
                months = asset.asset_type.depreciation.months if (asset.asset_type and asset.asset_type.depreciation) else None
                row[_('Depreciation Lifespan (Months)')] = str(months) if months else '-'
            if 'current_value' in active_cols:
                val = asset.current_value
                row[_('Depreciated Value')] = _money(val, getattr(asset, 'currency', None), active_tenant)
                
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
        # Scope the product list to the report's tenant(s) — every other branch
        # does this. Without it the Software branch ignored active_tenant/
        # filter_tenants and leaked every tenant's catalogue into MSP/scheduled
        # reports.
        # Software is allow_global_tenant: a null-tenant row is a shared catalogue
        # product visible to every tenant, so include it alongside the report's
        # own tenant(s) rather than dropping it.
        if filter_tenants:
            software_qs = software_qs.filter(Q(tenant__in=filter_tenants) | Q(tenant__isnull=True))
        elif active_tenant:
            software_qs = software_qs.filter(Q(tenant=active_tenant) | Q(tenant__isnull=True))

        # Per-product install/licence counts come from the model properties, which
        # derive their scope from the ambient tenant context — unreliable in a
        # scheduled/MSP run. Scope them explicitly to the report's tenant(s) so the
        # figures never include another tenant's installs or licences.
        report_tenant_ids = None
        if filter_tenants:
            report_tenant_ids = [t.pk for t in filter_tenants]
        elif active_tenant:
            report_tenant_ids = [active_tenant.pk]

        total_software = software_qs.count()
        
        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Software Products'), 'value': str(total_software)},
            ]
            
        # Annotate the per-product install/licence counts in a single query
        # instead of two .count() queries per row (N+1). distinct=True keeps each
        # count correct despite the join fan-out across the two relations; the
        # tenant filters mirror the row scoping above so the figures never include
        # another tenant's installs/licences.
        if report_tenant_ids is not None:
            installed_count_expr = Count(
                'installed_instances',
                filter=Q(installed_instances__asset__tenant_id__in=report_tenant_ids),
                distinct=True,
            )
            license_count_expr = Count(
                'licenses',
                filter=Q(licenses__deleted_at__isnull=True) & Q(licenses__tenant_id__in=report_tenant_ids),
                distinct=True,
            )
        else:
            installed_count_expr = Count('installed_instances', distinct=True)
            license_count_expr = Count(
                'licenses', filter=Q(licenses__deleted_at__isnull=True), distinct=True,
            )
        software_qs = software_qs.annotate(
            scoped_installed_count=installed_count_expr,
            scoped_license_count=license_count_expr,
        )

        category_counts = {}
        for soft in software_qs[:500]:
            row = {}
            installed = soft.scoped_installed_count
            licenses = soft.scoped_license_count
            
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

    elif template.report_type == ReportTemplate.REPORT_TYPE_CONTRACT_RENEWALS:
        from procurement.models import Contract
        from datetime import timedelta  # inline import: timedelta not yet at module level in compiler.py

        # select_related 'tenant' avoids N+1 in _record_currency when the
        # contract's own currency field is blank and falls back to tenant currency.
        contracts_qs = (
            Contract.objects.filter(deleted_at__isnull=True)
            .select_related('supplier', 'tenant')
            .prefetch_related('assets')
        )
        if filter_tenants:
            contracts_qs = contracts_qs.filter(tenant__in=filter_tenants)
        elif active_tenant:
            contracts_qs = contracts_qs.filter(tenant=active_tenant)

        total_contracts = contracts_qs.count()
        active_contracts_qs = contracts_qs.filter(status='active')
        total_active = active_contracts_qs.count()

        # Count contracts expiring within 30 days (active only; end_date - today in [0, 30]).
        today = timezone.now().date()
        in_30 = today + timedelta(days=30)
        expiring_soon_count = active_contracts_qs.filter(
            end_date__gte=today, end_date__lte=in_30
        ).count()

        # Annual spend bucketed per currency — no FX source, never sum across
        # currencies. Amortize per billing_cycle to a yearly equivalent, then
        # bucket by the record's resolved currency.
        annual_by_currency = {}
        for c in contracts_qs:
            if c.cost is None:
                continue
            cost_val = float(c.cost)
            bc = c.billing_cycle or 'annual'
            if bc == 'monthly':
                annual_cost = cost_val * 12.0
            elif bc == 'quarterly':
                annual_cost = cost_val * 4.0
            elif bc == 'biannual':
                annual_cost = cost_val * 2.0
            elif bc == 'annual':
                annual_cost = cost_val
            elif bc == 'multi_year':
                annual_cost = cost_val / 3.0
            else:  # onetime — include as-is; no sensible yearly figure
                annual_cost = cost_val
            cur = _record_currency(getattr(c, 'currency', None), active_tenant)
            annual_by_currency[cur] = annual_by_currency.get(cur, 0.0) + annual_cost

        # Supplier breakdown bucketed per (supplier, currency) for chart.
        supplier_costs = {}  # (supplier_name, currency) -> annual_cost
        for c in contracts_qs:
            if c.cost is None:
                continue
            cost_val = float(c.cost)
            bc = c.billing_cycle or 'annual'
            if bc == 'monthly':
                annual_cost = cost_val * 12.0
            elif bc == 'quarterly':
                annual_cost = cost_val * 4.0
            elif bc == 'biannual':
                annual_cost = cost_val * 2.0
            elif bc == 'annual':
                annual_cost = cost_val
            elif bc == 'multi_year':
                annual_cost = cost_val / 3.0
            else:
                annual_cost = cost_val
            cur = _record_currency(getattr(c, 'currency', None), active_tenant)
            supplier_name = c.supplier.name if c.supplier else _('Generic')
            supplier_costs[(supplier_name, cur)] = (
                supplier_costs.get((supplier_name, cur), 0.0) + annual_cost
            )

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Active Contracts'), 'value': str(total_active)},
                {'label': _('Expiring Within 30 Days'), 'value': str(expiring_soon_count)},
                {'label': _('Est. Annual Spend'), 'value': _format_per_currency(annual_by_currency)},
            ]

        for contract in contracts_qs[:500]:
            row = {}
            if 'contract_number' in active_cols:
                row[_('Contract #')] = contract.contract_number or '-'
            if 'contract_name' in active_cols:
                row[_('Contract Name')] = contract.name or '-'
            if 'contract_type' in active_cols:
                row[_('Contract Type')] = contract.get_contract_type_display()
            if 'contract_status' in active_cols:
                row[_('Contract Status')] = contract.get_status_display()
            if 'contract_supplier' in active_cols:
                row[_('Supplier')] = contract.supplier.name if contract.supplier else '-'
            if 'contract_start_date' in active_cols:
                row[_('Start Date')] = contract.start_date.strftime('%Y-%m-%d') if contract.start_date else '-'
            if 'contract_end_date' in active_cols:
                row[_('End Date')] = contract.end_date.strftime('%Y-%m-%d') if contract.end_date else '-'
            if 'contract_renewal_date' in active_cols:
                row[_('Renewal Date')] = contract.renewal_date.strftime('%Y-%m-%d') if contract.renewal_date else '-'
            if 'contract_days_until_expiry' in active_cols:
                row[_('Days Until Expiry')] = str(contract.days_until_expiry)
            if 'contract_cost' in active_cols:
                row[_('Contract Cost')] = _money(contract.cost, getattr(contract, 'currency', None), active_tenant)
            if 'contract_billing_cycle' in active_cols:
                row[_('Billing Cycle')] = contract.get_billing_cycle_display()
            if 'contract_auto_renew' in active_cols:
                row[_('Auto-Renew')] = _('Yes') if contract.auto_renew else _('No')
            if 'contract_covered_assets' in active_cols:
                row[_('Covered Assets')] = str(contract.assets.count())
            if 'contract_sla_response_time' in active_cols:
                row[_('SLA Response Time')] = contract.sla_response_time or '-'
            if 'contract_sla_resolution_time' in active_cols:
                row[_('SLA Resolution Time')] = contract.sla_resolution_time or '-'
            if 'contract_coverage_hours' in active_cols:
                row[_('Coverage Hours')] = contract.coverage_hours or '-'

            group_val = 'General'
            if template.group_by_field:
                if template.group_by_field == 'contract_status':
                    group_val = contract.get_status_display()
                elif template.group_by_field == 'contract_type':
                    group_val = contract.get_contract_type_display()
                elif template.group_by_field == 'contract_supplier':
                    group_val = contract.supplier.name if contract.supplier else _('No Supplier')
            row['_group_by'] = group_val
            rows.append(row)

        if not rows:
            row = {}
            for col in active_cols:
                if col == 'contract_number':
                    row[_('Contract #')] = 'CTR-MOCK-001'
                elif col == 'contract_name':
                    row[_('Contract Name')] = 'Hardware Support Agreement (Mock)'
                elif col == 'contract_type':
                    row[_('Contract Type')] = 'Support'
                elif col == 'contract_status':
                    row[_('Contract Status')] = 'Active'
                elif col == 'contract_supplier':
                    row[_('Supplier')] = 'Acme Corp'
                elif col == 'contract_start_date':
                    row[_('Start Date')] = '2026-01-01'
                elif col == 'contract_end_date':
                    row[_('End Date')] = '2026-12-31'
                elif col == 'contract_renewal_date':
                    row[_('Renewal Date')] = '2026-11-30'
                elif col == 'contract_days_until_expiry':
                    row[_('Days Until Expiry')] = '180'
                elif col == 'contract_cost':
                    row[_('Contract Cost')] = '12,000.00 EUR'
                elif col == 'contract_billing_cycle':
                    row[_('Billing Cycle')] = 'Annual'
                elif col == 'contract_auto_renew':
                    row[_('Auto-Renew')] = 'Yes'
                elif col == 'contract_covered_assets':
                    row[_('Covered Assets')] = '5'
                elif col == 'contract_sla_response_time':
                    row[_('SLA Response Time')] = '4 business hours'
                elif col == 'contract_sla_resolution_time':
                    row[_('SLA Resolution Time')] = 'Next business day'
                elif col == 'contract_coverage_hours':
                    row[_('Coverage Hours')] = '24x7'
            row['_group_by'] = (
                'Active' if template.group_by_field == 'contract_status'
                else 'Support' if template.group_by_field == 'contract_type'
                else 'Acme Corp' if template.group_by_field == 'contract_supplier'
                else 'General'
            )
            rows.append(row)
            if template.include_summary_cards:
                from django.conf import settings as _settings
                _mock_currency = (getattr(_settings, 'ITAMBOX_DEFAULT_CURRENCY', 'EUR') or 'EUR').upper()
                summary_cards = [
                    {'label': _('Active Contracts'), 'value': '1 (Mock)'},
                    {'label': _('Expiring Within 30 Days'), 'value': '0 (Mock)'},
                    {'label': _('Est. Annual Spend'), 'value': _format_per_currency({_mock_currency: 12000.0})},
                ]
            supplier_costs = {('Acme Corp', 'EUR'): 12000.0}

        # Render one bar per (supplier, currency). Qualify label with ISO code
        # when multiple currencies are in play (no FX source to combine bars).
        currencies_in_play = {cur for (_sup, cur) in supplier_costs.keys()}
        multi_currency = len(currencies_in_play) > 1
        chart_data = [
            {
                'label': f"{supplier_name} ({cur})" if multi_currency else supplier_name,
                'value': v,
                'display': _money(v, cur, active_tenant),
            }
            for (supplier_name, cur), v in supplier_costs.items()
        ]
        if template.include_distribution_chart:
            chart_svg = generate_bar_chart(chart_data, title=_("Annual Spend by Supplier"))
    elif template.report_type == ReportTemplate.REPORT_TYPE_WARRANTY_EXPIRATION:
        import datetime
        from assets.models.lifecycle import Warranty

        today = datetime.date.today()
        threshold_30 = today + datetime.timedelta(days=30)

        warranty_qs = Warranty.objects.filter(deleted_at__isnull=True).select_related('asset')
        if filter_tenants:
            warranty_qs = warranty_qs.filter(asset__tenant__in=filter_tenants)
        elif active_tenant:
            warranty_qs = warranty_qs.filter(asset__tenant=active_tenant)

        total_warranties = warranty_qs.count()
        expiring_soon = warranty_qs.filter(end_date__gte=today, end_date__lte=threshold_30).count()
        already_expired = warranty_qs.filter(end_date__lt=today).count()

        # Bucket warranty cost per currency for the summary card (no FX source).
        cost_by_currency = {}
        for w in warranty_qs:
            if w.cost is not None:
                code = _record_currency(getattr(w, 'currency', None), active_tenant)
                cost_by_currency[code] = cost_by_currency.get(code, 0) + w.cost

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Warranties'), 'value': str(total_warranties)},
                {'label': _('Expiring Within 30 Days'), 'value': str(expiring_soon)},
                {'label': _('Already Expired'), 'value': str(already_expired)},
                {'label': _('Total Warranty Cost'), 'value': _format_per_currency(cost_by_currency)},
            ]

        status_counts = {}
        for warranty in warranty_qs[:500]:
            end = warranty.end_date
            if end is None:
                days_remaining = '-'
                status_label = _('Unknown')
            else:
                delta = (end - today).days
                days_remaining = str(delta)
                if delta < 0:
                    status_label = _('Expired')
                elif delta <= 30:
                    status_label = _('Expiring Soon')
                else:
                    status_label = _('Active')

            row = {}
            if 'warranty_asset' in active_cols:
                row[_('Asset')] = warranty.asset.name if warranty.asset else '-'
            if 'warranty_type' in active_cols:
                row[_('Warranty Type')] = warranty.get_warranty_type_display()
            if 'warranty_provider' in active_cols:
                row[_('Provider')] = warranty.provider or '-'
            if 'warranty_start_date' in active_cols:
                row[_('Start Date')] = warranty.start_date.strftime('%Y-%m-%d') if warranty.start_date else '-'
            if 'warranty_end_date' in active_cols:
                row[_('End Date')] = end.strftime('%Y-%m-%d') if end else '-'
            if 'warranty_days_remaining' in active_cols:
                row[_('Days Remaining')] = days_remaining
            if 'warranty_status' in active_cols:
                row[_('Status')] = status_label
            if 'warranty_cost' in active_cols:
                row[_('Warranty Cost')] = _money(warranty.cost, getattr(warranty, 'currency', None), active_tenant)
            if 'warranty_reference' in active_cols:
                row[_('Reference')] = warranty.reference or '-'

            group_val = 'General'
            if template.group_by_field:
                if template.group_by_field == 'warranty_type':
                    group_val = warranty.get_warranty_type_display()
                elif template.group_by_field == 'status':
                    group_val = status_label
                elif template.group_by_field == 'asset':
                    group_val = warranty.asset.name if warranty.asset else _('Unassigned')
            row['_group_by'] = group_val
            rows.append(row)

            status_counts[status_label] = status_counts.get(status_label, 0) + 1

        if not rows:
            row = {}
            for col in active_cols:
                if col == 'warranty_asset':
                    row[_('Asset')] = 'MacBook Pro 16\" (Mock)'
                elif col == 'warranty_type':
                    row[_('Warranty Type')] = 'Hardware'
                elif col == 'warranty_provider':
                    row[_('Provider')] = 'Apple Care+'
                elif col == 'warranty_start_date':
                    row[_('Start Date')] = '2024-01-15'
                elif col == 'warranty_end_date':
                    row[_('End Date')] = '2027-01-14'
                elif col == 'warranty_days_remaining':
                    row[_('Days Remaining')] = '935'
                elif col == 'warranty_status':
                    row[_('Status')] = 'Active'
                elif col == 'warranty_cost':
                    row[_('Warranty Cost')] = '€299.00'
                elif col == 'warranty_reference':
                    row[_('Reference')] = 'REF-MOCK-0001'
            row['_group_by'] = (
                'Hardware' if template.group_by_field == 'warranty_type'
                else 'Active' if template.group_by_field == 'status'
                else 'MacBook Pro 16\" (Mock)' if template.group_by_field == 'asset'
                else 'General'
            )
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Total Warranties'), 'value': '1 (Mock)'},
                    {'label': _('Expiring Within 30 Days'), 'value': '0 (Mock)'},
                    {'label': _('Already Expired'), 'value': '0 (Mock)'},
                    {'label': _('Total Warranty Cost'), 'value': '€299.00'},
                ]
            status_counts = {_('Active'): 1}

        chart_data = [{'label': k, 'value': v} for k, v in status_counts.items()]
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(chart_data, title=_("Warranty Status Distribution"))
    elif template.report_type == ReportTemplate.REPORT_TYPE_ASSET_DISPOSAL_EOL:
        from assets.models.lifecycle import AssetDisposal

        disposal_qs = AssetDisposal.objects.filter(deleted_at__isnull=True).select_related(
            'asset', 'asset__tenant'
        )
        if filter_tenants:
            disposal_qs = disposal_qs.filter(asset__tenant__in=filter_tenants)
        elif active_tenant:
            disposal_qs = disposal_qs.filter(asset__tenant=active_tenant)

        total_disposals = disposal_qs.count()
        weee_count = disposal_qs.filter(weee_compliant=True).count()

        # Bucket proceeds per currency — no FX source, never sum across currencies.
        proceeds_by_currency = {}
        for d in disposal_qs:
            if d.proceeds is not None:
                code = _record_currency(getattr(d, 'currency', None), active_tenant)
                proceeds_by_currency[code] = proceeds_by_currency.get(code, 0) + d.proceeds

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Disposals'), 'value': str(total_disposals)},
                {'label': _('WEEE Compliant'), 'value': str(weee_count)},
                {'label': _('Total Proceeds'), 'value': _format_per_currency(proceeds_by_currency)},
            ]

        method_counts = {}
        for disposal in disposal_qs[:500]:
            row = {}
            if 'disposal_asset' in active_cols:
                row[_('Asset')] = str(disposal.asset) if disposal.asset else '-'
            if 'disposal_date' in active_cols:
                row[_('Disposal Date')] = disposal.disposal_date.strftime('%Y-%m-%d') if disposal.disposal_date else '-'
            if 'disposal_method' in active_cols:
                row[_('Disposal Method')] = disposal.get_disposal_method_display()
            if 'disposal_sanitization_method' in active_cols:
                row[_('Data Sanitization Method')] = disposal.get_data_sanitization_method_display()
            if 'disposal_sanitization_certificate' in active_cols:
                row[_('Sanitization Certificate')] = disposal.sanitization_certificate or '-'
            if 'disposal_sanitized_by' in active_cols:
                row[_('Sanitized By')] = disposal.sanitized_by or '-'
            if 'disposal_recipient' in active_cols:
                row[_('Recipient')] = disposal.recipient or '-'
            if 'disposal_proceeds' in active_cols:
                row[_('Proceeds')] = _money(disposal.proceeds, getattr(disposal, 'currency', None), active_tenant)
            if 'disposal_weee_compliant' in active_cols:
                row[_('WEEE Compliant')] = _('Yes') if disposal.weee_compliant else _('No')
            if 'disposal_notes' in active_cols:
                row[_('Notes')] = disposal.notes or '-'

            group_val = 'General'
            if template.group_by_field:
                if template.group_by_field == 'disposal_method':
                    group_val = disposal.get_disposal_method_display()
                elif template.group_by_field == 'disposal_sanitization_method':
                    group_val = disposal.get_data_sanitization_method_display()
                elif template.group_by_field == 'disposal_weee_compliant':
                    group_val = _('WEEE Compliant') if disposal.weee_compliant else _('Not WEEE Compliant')
            row['_group_by'] = group_val
            rows.append(row)

            m_label = disposal.get_disposal_method_display()
            method_counts[m_label] = method_counts.get(m_label, 0) + 1

        if not rows:
            row = {}
            for col in active_cols:
                if col == 'disposal_asset':
                    row[_('Asset')] = 'ASSET-MOCK-001 (Mock)'
                elif col == 'disposal_date':
                    row[_('Disposal Date')] = '2026-06-01'
                elif col == 'disposal_method':
                    row[_('Disposal Method')] = 'Recycle / WEEE'
                elif col == 'disposal_sanitization_method':
                    row[_('Data Sanitization Method')] = 'NIST Purge (cryptographic or ATA Secure Erase)'
                elif col == 'disposal_sanitization_certificate':
                    row[_('Sanitization Certificate')] = 'CERT-2026-001'
                elif col == 'disposal_sanitized_by':
                    row[_('Sanitized By')] = 'SecureWipe GmbH'
                elif col == 'disposal_recipient':
                    row[_('Recipient')] = 'GreenIT Recyclers'
                elif col == 'disposal_proceeds':
                    row[_('Proceeds')] = '150,00\xa0€'
                elif col == 'disposal_weee_compliant':
                    row[_('WEEE Compliant')] = 'Yes'
                elif col == 'disposal_notes':
                    row[_('Notes')] = '-'
            row['_group_by'] = (
                'Recycle / WEEE' if template.group_by_field == 'disposal_method'
                else 'NIST Purge (cryptographic or ATA Secure Erase)' if template.group_by_field == 'disposal_sanitization_method'
                else 'WEEE Compliant' if template.group_by_field == 'disposal_weee_compliant'
                else 'General'
            )
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Total Disposals'), 'value': '1 (Mock)'},
                    {'label': _('WEEE Compliant'), 'value': '1 (Mock)'},
                    {'label': _('Total Proceeds'), 'value': '150,00\xa0€'},
                ]
            method_counts = {'Recycle / WEEE': 1}

        chart_data = [{'label': k, 'value': v} for k, v in method_counts.items()]
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(chart_data, title=_("Disposal Method Distribution"))
    elif template.report_type == ReportTemplate.REPORT_TYPE_HARDWARE_INVENTORY:
        # Non-asset hardware stock: Accessories, Consumables, Components. Each is its
        # own tenant-scoped model (allow_global_tenant=True, so a null-tenant row is a
        # shared-catalogue item visible to every tenant -- include it like software).
        # Stock figures come from each model's total_stock/available properties
        # (per-item aggregate of stock rows); with the [:500] cap this is a bounded N+1.
        from inventory.models import Accessory, Consumable, Component

        def _hw_scope(qs):
            if filter_tenants:
                return qs.filter(Q(tenant__in=filter_tenants) | Q(tenant__isnull=True))
            if active_tenant:
                return qs.filter(Q(tenant=active_tenant) | Q(tenant__isnull=True))
            return qs

        hw_models = [
            (_('Accessory'), _hw_scope(Accessory.objects.filter(deleted_at__isnull=True).select_related('manufacturer', 'category'))),
            (_('Consumable'), _hw_scope(Consumable.objects.filter(deleted_at__isnull=True).select_related('manufacturer', 'category'))),
            (_('Component'), _hw_scope(Component.objects.filter(deleted_at__isnull=True).select_related('manufacturer', 'category'))),
        ]

        type_counts = {label: qs.count() for label, qs in hw_models}
        zero_stock_count = 0

        for type_label, qs in hw_models:
            for item in qs[:500]:
                total = item.total_stock
                avail = item.available
                if total <= 0:
                    zero_stock_count += 1
                row = {}
                if 'hw_item_type' in active_cols:
                    row[_('Item Type')] = type_label
                if 'hw_name' in active_cols:
                    row[_('Name')] = item.name or '-'
                if 'hw_manufacturer' in active_cols:
                    row[_('Manufacturer')] = item.manufacturer.name if item.manufacturer else '-'
                if 'hw_category' in active_cols:
                    row[_('Category')] = item.category.name if item.category else '-'
                if 'hw_part_number' in active_cols:
                    row[_('Part Number')] = item.part_number or '-'
                if 'hw_total_stock' in active_cols:
                    row[_('Total Stock')] = str(total)
                if 'hw_available' in active_cols:
                    row[_('Available')] = str(avail)
                if 'hw_min_qty' in active_cols:
                    row[_('Safety Threshold')] = str(item.min_qty)
                if 'hw_status' in active_cols:
                    if total <= 0:
                        row[_('Stock Status')] = _('Out of Stock')
                    elif item.min_qty and avail <= item.min_qty:
                        row[_('Stock Status')] = _('Low Stock')
                    else:
                        row[_('Stock Status')] = _('In Stock')
                group_val = type_label
                if template.group_by_field == 'manufacturer':
                    group_val = item.manufacturer.name if item.manufacturer else _('Generic')
                elif template.group_by_field == 'category':
                    group_val = item.category.name if item.category else _('Uncategorized')
                row['_group_by'] = group_val
                rows.append(row)

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Accessory SKUs'), 'value': str(type_counts.get(_('Accessory'), 0))},
                {'label': _('Consumable SKUs'), 'value': str(type_counts.get(_('Consumable'), 0))},
                {'label': _('Component SKUs'), 'value': str(type_counts.get(_('Component'), 0))},
                {'label': _('Items at Zero Stock'), 'value': str(zero_stock_count)},
            ]

        if not rows:
            row = {}
            for col in active_cols:
                if col == 'hw_item_type':
                    row[_('Item Type')] = 'Accessory'
                elif col == 'hw_name':
                    row[_('Name')] = 'USB-C Dock (Mock)'
                elif col == 'hw_manufacturer':
                    row[_('Manufacturer')] = 'Dell'
                elif col == 'hw_category':
                    row[_('Category')] = 'Docking'
                elif col == 'hw_part_number':
                    row[_('Part Number')] = 'WD19S'
                elif col == 'hw_total_stock':
                    row[_('Total Stock')] = '24'
                elif col == 'hw_available':
                    row[_('Available')] = '18'
                elif col == 'hw_min_qty':
                    row[_('Safety Threshold')] = '5'
                elif col == 'hw_status':
                    row[_('Stock Status')] = 'In Stock'
            row['_group_by'] = 'Accessory'
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Accessory SKUs'), 'value': '1 (Mock)'},
                    {'label': _('Consumable SKUs'), 'value': '0'},
                    {'label': _('Component SKUs'), 'value': '0'},
                    {'label': _('Items at Zero Stock'), 'value': '0'},
                ]
            type_counts = {_('Accessory'): 1}

        chart_data = [{'label': k, 'value': v} for k, v in type_counts.items() if v > 0]
        if not chart_data:
            chart_data = [{'label': _('Accessory'), 'value': 1}]
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(chart_data, title=_("Hardware Inventory by Type"))
    elif template.report_type == ReportTemplate.REPORT_TYPE_CUSTODY_COMPLIANCE:
        from compliance.models import CustodyReceipt

        # CustodyReceipt has no tenant FK of its own; scope via asset__tenant.
        # It does not inherit SoftDeleteMixin, so there is no deleted_at field to filter.
        receipts_qs = CustodyReceipt.objects.select_related('asset', 'holder')
        if filter_tenants:
            receipts_qs = receipts_qs.filter(asset__tenant__in=filter_tenants)
        elif active_tenant:
            receipts_qs = receipts_qs.filter(asset__tenant=active_tenant)

        total_receipts = receipts_qs.count()
        pending_count = receipts_qs.filter(acceptance_status=CustodyReceipt.STATUS_PENDING).count()
        accepted_count = receipts_qs.filter(acceptance_status=CustodyReceipt.STATUS_ACCEPTED).count()
        acceptance_rate = round((accepted_count / total_receipts * 100), 1) if total_receipts > 0 else 0.0

        if template.include_summary_cards:
            summary_cards = [
                {'label': _('Total Receipts'), 'value': str(total_receipts)},
                {'label': _('Pending Sign-offs'), 'value': str(pending_count)},
                {'label': _('Acceptance Rate'), 'value': f"{acceptance_rate}%"},
            ]

        status_counts = {}
        for receipt in receipts_qs[:500]:
            row = {}
            if 'custody_asset' in active_cols:
                row[_('Asset')] = str(receipt.asset) if receipt.asset else '-'
            if 'custody_holder' in active_cols:
                row[_('Holder')] = str(receipt.holder) if receipt.holder else '-'
            if 'custody_status' in active_cols:
                row[_('Acceptance Status')] = receipt.get_acceptance_status_display()
            if 'custody_accepted_date' in active_cols:
                row[_('Accepted Date')] = receipt.accepted_date.strftime('%Y-%m-%d %H:%M') if receipt.accepted_date else '-'
            if 'custody_eula_version' in active_cols:
                row[_('EULA Version')] = receipt.eula_version or '-'
            if 'custody_signature_provider' in active_cols:
                row[_('Signature Provider')] = receipt.signature_provider or '-'
            if 'custody_qms_reference' in active_cols:
                row[_('QMS Reference')] = receipt.qms_reference or '-'
            if 'custody_ip_address' in active_cols:
                row[_('IP Address')] = str(receipt.ip_address) if receipt.ip_address else '-'
            if 'custody_created_date' in active_cols:
                row[_('Created Date')] = receipt.created_date.strftime('%Y-%m-%d') if receipt.created_date else '-'

            group_val = 'General'
            if template.group_by_field:
                if template.group_by_field == 'custody_status':
                    group_val = receipt.get_acceptance_status_display()
                elif template.group_by_field == 'custody_signature_provider':
                    group_val = receipt.signature_provider or _('Unknown')
                elif template.group_by_field == 'custody_eula_version':
                    group_val = receipt.eula_version or _('Unknown')
            row['_group_by'] = group_val
            rows.append(row)

            # Aggregate chart data
            status_label = receipt.get_acceptance_status_display()
            status_counts[status_label] = status_counts.get(status_label, 0) + 1

        if not rows:
            row = {}
            for col in active_cols:
                if col == 'custody_asset':
                    row[_('Asset')] = 'AST-MOCK-001 — MacBook Pro (Mock)'
                elif col == 'custody_holder':
                    row[_('Holder')] = 'Alex Dev (Mock)'
                elif col == 'custody_status':
                    row[_('Acceptance Status')] = 'Accepted'
                elif col == 'custody_accepted_date':
                    row[_('Accepted Date')] = '2026-06-01 09:00'
                elif col == 'custody_eula_version':
                    row[_('EULA Version')] = '1.0'
                elif col == 'custody_signature_provider':
                    row[_('Signature Provider')] = 'local'
                elif col == 'custody_qms_reference':
                    row[_('QMS Reference')] = 'QMS-2026-001'
                elif col == 'custody_ip_address':
                    row[_('IP Address')] = '192.168.1.10'
                elif col == 'custody_created_date':
                    row[_('Created Date')] = '2026-06-01'
            row['_group_by'] = 'Accepted' if template.group_by_field == 'custody_status' else 'local' if template.group_by_field == 'custody_signature_provider' else '1.0' if template.group_by_field == 'custody_eula_version' else 'General'
            rows.append(row)
            if template.include_summary_cards:
                summary_cards = [
                    {'label': _('Total Receipts'), 'value': '1 (Mock)'},
                    {'label': _('Pending Sign-offs'), 'value': '0 (Mock)'},
                    {'label': _('Acceptance Rate'), 'value': '100.0% (Mock)'},
                ]
            status_counts = {_('Accepted'): 1}

        chart_data = [{'label': k, 'value': v} for k, v in status_counts.items()]
        if template.include_distribution_chart:
            chart_svg = generate_doughnut_chart(chart_data, title=_("Receipt Acceptance Status Distribution"))

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
