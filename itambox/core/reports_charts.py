# itambox/core/reports_charts.py
import math
from django.utils import timezone
from django.utils.translation import gettext as _
from django.db.models import Count, Q
from django.contrib.contenttypes.models import ContentType

def generate_doughnut_chart(data, title=""):
    """
    Generates a beautifully styled, self-contained SVG doughnut chart.
    data: list of dicts like [{'label': 'Deployed', 'value': 45}, ...]
    """
    # Filter out zero or negative values
    data = [item for item in data if item.get('value', 0) > 0]
    if not data:
        return f"""
        <svg viewBox="0 0 480 220" width="100%" height="220" xmlns="http://www.w3.org/2000/svg" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <rect width="480" height="220" rx="12" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1"/>
            <text x="240" y="115" text-anchor="middle" font-size="13" fill="#64748b" font-weight="500">{_("No data available for charting")}</text>
        </svg>
        """

    total = sum(item['value'] for item in data)
    r = 60
    circumference = 2 * math.pi * r  # ~376.99
    
    colors = ['#206bc4', '#4263eb', '#2fb344', '#f76707', '#0ca678', '#ae3ec9', '#d63939', '#f59f00', '#74b816', '#66d9e8']
    
    svg_parts = []
    svg_parts.append('<svg viewBox="0 0 480 220" width="100%" height="220" xmlns="http://www.w3.org/2000/svg" style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">')
    
    if title:
        svg_parts.append(f'<text x="20" y="25" font-size="13" font-weight="700" fill="#0f172a" letter-spacing="-0.2px">{title}</text>')
    
    # Doughnut circle base
    svg_parts.append('<g transform="rotate(-90 110 120)">')
    
    accumulated_percent = 0
    for idx, item in enumerate(data):
        val = item['value']
        pct = val / total
        color = colors[idx % len(colors)]
        
        stroke_dasharray = f"{pct * circumference:.2f} {circumference:.2f}"
        stroke_dashoffset = f"{-accumulated_percent * circumference:.2f}"
        
        svg_parts.append(
            f'<circle cx="110" cy="120" r="{r}" fill="transparent" '
            f'stroke="{color}" stroke-width="16" '
            f'stroke-dasharray="{stroke_dasharray}" stroke-dashoffset="{stroke_dashoffset}" />'
        )
        accumulated_percent += pct
        
    svg_parts.append('</g>')
    
    # Doughnut Center Cutout and Text (Total count)
    svg_parts.append(f'<circle cx="110" cy="120" r="{r - 8}" fill="#ffffff" />')
    svg_parts.append(f'<text x="110" y="118" text-anchor="middle" font-size="18" font-weight="800" fill="#0f172a">{total:,}</text>')
    svg_parts.append(f'<text x="110" y="132" text-anchor="middle" font-size="9" font-weight="600" fill="#64748b" text-transform="uppercase" letter-spacing="0.5px">{_("Total")}</text>')
    
    # Legend Section (right side, starting at x=230)
    legend_y_start = 45
    legend_row_height = 25
    
    # Show at most 6 items in the legend, group remainder under "Other"
    visible_items = data[:6]
    other_items = data[6:]
    
    if other_items:
        other_sum = sum(item['value'] for item in other_items)
        visible_items.append({'label': _('Other'), 'value': other_sum})
        
    for idx, item in enumerate(visible_items):
        y_pos = legend_y_start + (idx * legend_row_height)
        color = '#64748b' if item['label'] == _('Other') or item['label'] == 'Other' else colors[idx % len(colors)]
        pct_str = f"{(item['value']/total)*100:.1f}%"
        
        # Color indicator circle
        svg_parts.append(f'<circle cx="235" cy="{y_pos - 4}" r="5" fill="{color}" />')
        # Label text
        svg_parts.append(f'<text x="250" y="{y_pos}" font-size="11" font-weight="500" fill="#334155">{item["label"]}</text>')
        # Value text (right-aligned at x=390)
        svg_parts.append(f'<text x="390" y="{y_pos}" font-size="11" font-weight="700" fill="#0f172a" text-anchor="end">{item["value"]:,}</text>')
        # Percentage text (right-aligned at x=450)
        svg_parts.append(f'<text x="450" y="{y_pos}" font-size="10" font-weight="600" fill="#64748b" text-anchor="end">{pct_str}</text>')
        
    svg_parts.append('</svg>')
    return '\n'.join(svg_parts)


def generate_bar_chart(data, title=""):
    """
    Generates a beautifully styled horizontal SVG bar chart.
    data: list of dicts like [{'label': 'Office 365', 'value': 1200.00}, ...]
    """
    # Filter out zero or negative values
    data = [item for item in data if item.get('value', 0) > 0]
    if not data:
        return f"""
        <svg viewBox="0 0 480 220" width="100%" height="220" xmlns="http://www.w3.org/2000/svg" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <rect width="480" height="220" rx="12" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1"/>
            <text x="240" y="115" text-anchor="middle" font-size="13" fill="#64748b" font-weight="500">{_("No data available for charting")}</text>
        </svg>
        """

    # Keep top 5 items
    data = sorted(data, key=lambda x: x['value'], reverse=True)[:5]
    max_val = max(item['value'] for item in data)
    
    colors = ['#206bc4', '#4263eb', '#2fb344', '#f76707', '#0ca678']
    
    svg_parts = []
    svg_parts.append('<svg viewBox="0 0 480 220" width="100%" height="220" xmlns="http://www.w3.org/2000/svg" style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">')
    
    if title:
        svg_parts.append(f'<text x="20" y="25" font-size="13" font-weight="700" fill="#0f172a" letter-spacing="-0.2px">{title}</text>')
        
    y_start = 50
    row_height = 32
    max_bar_width = 240
    
    for idx, item in enumerate(data):
        val = item['value']
        label = item['label']
        color = colors[idx % len(colors)]
        
        # Calculate bar width proportion
        bar_width = max(int((val / max_val) * max_bar_width), 8)
        y_pos = y_start + (idx * row_height)
        
        # Truncate label if too long
        short_label = label if len(label) <= 18 else label[:16] + "..."
        
        # Label text
        svg_parts.append(f'<text x="20" y="{y_pos + 12}" font-size="11" font-weight="600" fill="#475569" text-anchor="start">{short_label}</text>')
        
        # Bar background
        svg_parts.append(f'<rect x="140" y="{y_pos}" width="{max_bar_width}" height="16" rx="4" fill="#f1f5f9" />')
        
        # Actual colored bar
        svg_parts.append(f'<rect x="140" y="{y_pos}" width="{bar_width}" height="16" rx="4" fill="{color}" />')
        
        # Value label (right of bar)
        val_str = f"${val:,.2f}" if (val >= 100 or isinstance(val, float)) and '.' in str(val) else f"{int(val):,}"
        if not val_str.startswith('$') and isinstance(val, float):
             val_str = f"${val:,.2f}"
        svg_parts.append(f'<text x="{145 + bar_width}" y="{y_pos + 12}" font-size="11" font-weight="700" fill="#0f172a" text-anchor="start">{val_str}</text>')
        
    svg_parts.append('</svg>')
    return '\n'.join(svg_parts)


def get_polished_system_html_template():
    """
    Returns the highly-polished, HTML no-code template.
    Includes print stylesheets and inline visual elements.
    """
    return """{% load utility_tags %}
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            color: #1e293b;
            line-height: 1.5;
            background-color: #f8fafc;
            margin: 0;
            padding: 24px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #ffffff;
            padding: 32px;
            text-align: left;
        }
        .header h2 {
            margin: 0;
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.5px;
        }
        .header p {
            margin: 8px 0 0 0;
            opacity: 0.85;
            font-size: 14px;
        }
        .content {
            padding: 32px;
        }
        .meta {
            font-size: 12px;
            color: #64748b;
            margin-bottom: 24px;
            font-family: monospace;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .metrics {
            display: flex;
            flex-wrap: wrap;
            margin-bottom: 32px;
            gap: 16px;
        }
        .metric-card {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px;
            flex: 1;
            min-width: 140px;
            text-align: center;
        }
        .metric-card .value {
            font-size: 22px;
            font-weight: 700;
            color: #0f172a;
            margin-top: 4px;
        }
        .metric-card .label {
            font-size: 11px;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 600;
        }
        .group-title {
            font-size: 14px;
            font-weight: 700;
            color: #475569;
            background: #f1f5f9;
            padding: 8px 12px;
            border-radius: 6px;
            margin-top: 28px;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 24px;
        }
        th, td {
            text-align: left;
            border-bottom: 1px solid #e2e8f0;
        }
        th {
            color: #475569;
            font-weight: 600;
            background: #f8fafc;
        }
        
        /* Layout Presets styling */
        {% if is_compact %}
        th, td {
            padding: 6px 8px;
            font-size: 11px;
        }
        {% else %}
        th, td {
            padding: 12px 14px;
            font-size: 13px;
        }
        {% endif %}
        
        {% if is_financial %}
        td:contains('$') {
            font-weight: 700;
            color: #0f766e;
        }
        {% endif %}
        
        .footer {
            background: #f8fafc;
            padding: 24px;
            text-align: center;
            font-size: 12px;
            color: #64748b;
            border-top: 1px solid #e2e8f0;
        }

        /* Print Media Stylesheet */
        @media print {
            body {
                background-color: #ffffff !important;
                color: #000000 !important;
                padding: 0 !important;
                margin: 0 !important;
            }
            .container {
                border: none !important;
                box-shadow: none !important;
                max-width: 100% !important;
                margin: 0 !important;
                padding: 0 !important;
            }
            .header {
                background: none !important;
                color: #000000 !important;
                padding: 0 0 20px 0 !important;
                border-bottom: 2px solid #0f172a !important;
            }
            .header h2 {
                color: #000000 !important;
                font-size: 28px !important;
            }
            .header p {
                color: #475569 !important;
                opacity: 1 !important;
            }
            .content {
                padding: 20px 0 !important;
            }
            .metrics {
                margin-bottom: 20px !important;
                gap: 12px !important;
            }
            .metric-card {
                border: 1px solid #cbd5e1 !important;
                background: #ffffff !important;
                padding: 12px !important;
            }
            .metric-card .value {
                color: #000000 !important;
            }
            table {
                page-break-inside: auto !important;
            }
            tr {
                page-break-inside: avoid !important;
                page-break-after: auto !important;
            }
            thead {
                display: table-header-group !important;
            }
            th {
                background: #f1f5f9 !important;
                color: #000000 !important;
                border-bottom: 2px solid #cbd5e1 !important;
            }
            td {
                border-bottom: 1px solid #e2e8f0 !important;
            }
            .footer {
                background: none !important;
                border-top: 1px solid #cbd5e1 !important;
                color: #64748b !important;
                padding: 12px 0 !important;
            }
            .no-print, .print-btn {
                display: none !important;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>{{ report_name }}</h2>
            <p>{{ description|default:"Visual inventory compilation." }}</p>
        </div>
        <div class="content">
            <div class="meta">
                <div>
                    GENERATED: {{ generated_at|date:"Y-m-d H:i:s" }} UTC | STYLE: {{ style_preset|upper }}
                </div>
                <button type="button" class="no-print" onclick="window.print();" style="cursor: pointer; display: inline-flex; align-items: center; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px 12px; font-size: 11px; font-weight: 600; color: #334155; transition: all 0.15s ease;">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 4px; vertical-align: middle;"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M17 17h2a2 2 0 0 0 2 -2v-4a2 2 0 0 0 -2 -2h-14a2 2 0 0 0 -2 2v4a2 2 0 0 0 2 2h2" /><path d="M17 9v-4a2 2 0 0 0 -2 -2h-6a2 2 0 0 0 -2 2v4" /><path d="M7 13m0 2a2 2 0 0 1 2 -2h6a2 2 0 0 1 2 2v4a2 2 0 0 1 -2 2h-6a2 2 0 0 1 -2 -2z" /></svg>
                    Print Report
                </button>
            </div>
            
            {% if summary_cards %}
            <div class="metrics">
                {% for card in summary_cards %}
                <div class="metric-card">
                    <div class="label">{{ card.label }}</div>
                    <div class="value">{{ card.value }}</div>
                </div>
                {% endfor %}
            </div>
            {% endif %}

            {% if distribution_chart %}
            <div style="margin-bottom: 32px;" class="chart-wrapper">
                {{ distribution_chart|safe }}
            </div>
            {% endif %}
            
            {% for group_name, group_rows in grouped_data.items %}
                {% if group_name != 'General' %}
                <div class="group-title">{{ group_name }}</div>
                {% endif %}
                <table>
                    <thead>
                        <tr>
                            {% for head in headers %}
                            <th>{{ head }}</th>
                            {% endfor %}
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in group_rows %}
                        <tr>
                            {% for head in headers %}
                            <td>{{ row|lookup:head }}</td>
                            {% endfor %}
                        </tr>
                        {% empty %}
                        <tr>
                            <td colspan="{{ headers|length }}" style="text-align: center; color: #64748b; padding: 20px;">
                                No records found.
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            {% endfor %}
        </div>
        <div class="footer">
            Sent automatically by ITAMbox — IT Asset Management Console.
        </div>
    </div>
    <script>
        if (window.location.search.includes('print=true')) {
            window.onload = function() {
                window.print();
            }
        }
    </script>
</body>
</html>
"""


def compile_report_context(template, active_tenant=None, filter_tenants=None):
    """
    Unified report compiler that aggregates assets/licenses/subscriptions,
    applies tenant scoping and filter constellations, compiles summary card metrics,
    orders selected data columns, and renders self-contained SVG distribution charts.
    """
    from core.models import ReportTemplate
    
    # Resolve active columns sequence
    active_cols = template.included_columns or []
    if not active_cols:
        if template.report_type == 'asset_summary':
            active_cols = ['asset_tag', 'name', 'status', 'location', 'assigned_to']
        elif template.report_type == 'license_utilization':
            active_cols = ['license_name', 'software', 'seats', 'assigned_seats', 'available_seats', 'utilization_rate']
        elif template.report_type == 'asset_maintenance':
            active_cols = ['maintenance_title', 'maintenance_asset', 'maintenance_type', 'maintenance_status', 'maintenance_cost']
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
        'maintenance_downtime': _('Downtime (Days)')
    }
    
    headers = [headers_map[col] for col in active_cols if col in headers_map]

    if template.report_type == ReportTemplate.REPORT_TYPE_ASSET_SUMMARY:
        from assets.models import Asset
        
        assets_qs = Asset.objects.filter(deleted_at__isnull=True)
        if filter_tenants:
            assets_qs = assets_qs.filter(tenant__in=filter_tenants)
        elif active_tenant:
            assets_qs = assets_qs.filter(tenant=active_tenant)
            
        assets_qs = assets_qs.select_related('asset_type', 'asset_type__manufacturer', 'status').prefetch_related('assignments', 'assignments__assigned_to')
        
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
        from compliance.models import AssetMaintenance
        
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
