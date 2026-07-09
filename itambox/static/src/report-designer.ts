/**
 * ITAMbox — Report Template Designer and Column Sequence Manager.
 */
(function () {
  function generateJinja2FromForm(): string {
    const descInput = document.querySelector('textarea[name="description"]') as HTMLTextAreaElement | null;
    const typeSelect = document.querySelector('select[name="report_type"]') as HTMLSelectElement | null;

    const description = descInput ? descInput.value || gettext('Visual inventory compilation.') : gettext('Visual inventory compilation.');
    const reportType = typeSelect ? typeSelect.value : 'asset_summary';
    
    // Get selected checkboxes
    const checkedInputs = document.querySelectorAll('input[name="included_columns"]:checked') as NodeListOf<HTMLInputElement>;
    let selectedCols = Array.from(checkedInputs).map(el => el.value);
    
    if (selectedCols.length === 0) {
      // fallback defaults based on report type
      if (reportType === 'asset_summary') {
        selectedCols = ['asset_tag', 'name', 'status', 'location', 'assigned_to'];
      } else if (reportType === 'license_utilization') {
        selectedCols = ['license_name', 'software', 'seats', 'assigned_seats', 'available_seats', 'utilization_rate'];
      } else if (reportType === 'asset_maintenance') {
        selectedCols = ['maintenance_title', 'maintenance_asset', 'maintenance_type', 'maintenance_status', 'maintenance_cost'];
      } else if (reportType === 'asset_depreciation') {
        selectedCols = ['asset_tag', 'name', 'purchase_cost', 'salvage_value', 'depreciation_months', 'current_value'];
      } else if (reportType === 'software_inventory') {
        selectedCols = ['software_name', 'manufacturer', 'version', 'category', 'license_type', 'installed_count', 'license_count'];
      } else if (reportType === 'contract_renewals') {
        selectedCols = ['contract_number', 'contract_name', 'contract_type', 'contract_status', 'contract_supplier', 'contract_end_date', 'contract_days_until_expiry', 'contract_cost'];
      } else if (reportType === 'warranty_expiration') {
        selectedCols = ['warranty_asset', 'warranty_type', 'warranty_provider', 'warranty_end_date', 'warranty_days_remaining', 'warranty_status'];
      } else if (reportType === 'asset_disposal_eol') {
        selectedCols = ['disposal_asset', 'disposal_date', 'disposal_method', 'disposal_sanitization_method', 'disposal_weee_compliant', 'disposal_proceeds'];
      } else if (reportType === 'hardware_inventory') {
        selectedCols = ['hw_item_type', 'hw_name', 'hw_manufacturer', 'hw_category', 'hw_total_stock', 'hw_available', 'hw_status'];
      } else if (reportType === 'custody_compliance') {
        selectedCols = ['custody_asset', 'custody_holder', 'custody_status', 'custody_accepted_date', 'custody_eula_version', 'custody_signature_provider'];
      } else {
        selectedCols = ['subscription_name', 'provider', 'billing_cycle', 'cost', 'end_date'];
      }
    }
    
    // Header display names map
    const headersMap: Record<string, string> = {
      'asset_tag': gettext('Asset Tag'),
      'name': gettext('Asset Name'),
      'manufacturer': gettext('Manufacturer'),
      'model': gettext('Model'),
      'serial_number': gettext('Serial Number'),
      'status': gettext('Status Label'),
      'location': gettext('Location'),
      'assigned_to': gettext('Asset Holder'),
      'purchase_cost': gettext('Purchase Cost'),
      'purchase_date': gettext('Purchase Date'),
      'warranty_months': gettext('Warranty (Months)'),
      'license_name': gettext('License Name'),
      'software': gettext('Software'),
      'seats': gettext('Total Seats'),
      'assigned_seats': gettext('Assigned Seats'),
      'available_seats': gettext('Available Seats'),
      'utilization_rate': gettext('Utilization Rate'),
      'subscription_name': gettext('Subscription Name'),
      'provider': gettext('Provider'),
      'billing_cycle': gettext('Billing Cycle'),
      'cost': gettext('Cost'),
      'end_date': gettext('End Date'),
      'salvage_value': gettext('Salvage Value'),
      'depreciation_months': gettext('Depreciation Lifespan (Months)'),
      'current_value': gettext('Depreciated Value'),
      'software_name': gettext('Software Product'),
      'installed_count': gettext('Installed Count'),
      'license_count': gettext('License Count'),
      'maintenance_title': gettext('Maintenance Title'),
      'maintenance_asset': gettext('Asset'),
      'maintenance_type': gettext('Type'),
      'maintenance_status': gettext('Status'),
      'maintenance_cost': gettext('Cost'),
      'contract_number': gettext('Contract #'),
      'contract_name': gettext('Contract Name'),
      'contract_type': gettext('Contract Type'),
      'contract_status': gettext('Contract Status'),
      'contract_supplier': gettext('Supplier'),
      'contract_start_date': gettext('Start Date'),
      'contract_end_date': gettext('End Date'),
      'contract_renewal_date': gettext('Renewal Date'),
      'contract_days_until_expiry': gettext('Days Until Expiry'),
      'contract_cost': gettext('Contract Cost'),
      'contract_billing_cycle': gettext('Billing Cycle'),
      'contract_auto_renew': gettext('Auto-Renew'),
      'contract_covered_assets': gettext('Covered Assets'),
      'contract_sla_response_time': gettext('SLA Response Time'),
      'contract_sla_resolution_time': gettext('SLA Resolution Time'),
      'contract_coverage_hours': gettext('Coverage Hours'),
      'warranty_asset': gettext('Asset'),
      'warranty_type': gettext('Warranty Type'),
      'warranty_provider': gettext('Provider'),
      'warranty_start_date': gettext('Start Date'),
      'warranty_end_date': gettext('End Date'),
      'warranty_days_remaining': gettext('Days Remaining'),
      'warranty_status': gettext('Status'),
      'warranty_cost': gettext('Warranty Cost'),
      'warranty_reference': gettext('Reference'),
      'disposal_asset': gettext('Asset'),
      'disposal_date': gettext('Disposal Date'),
      'disposal_method': gettext('Disposal Method'),
      'disposal_sanitization_method': gettext('Data Sanitization Method'),
      'disposal_sanitization_certificate': gettext('Sanitization Certificate'),
      'disposal_sanitized_by': gettext('Sanitized By'),
      'disposal_recipient': gettext('Recipient'),
      'disposal_proceeds': gettext('Proceeds'),
      'disposal_weee_compliant': gettext('WEEE Compliant'),
      'disposal_notes': gettext('Notes'),
      'hw_item_type': gettext('Item Type'),
      'hw_name': gettext('Name'),
      'hw_manufacturer': gettext('Manufacturer'),
      'hw_category': gettext('Category'),
      'hw_part_number': gettext('Part Number'),
      'hw_total_stock': gettext('Total Stock'),
      'hw_available': gettext('Available'),
      'hw_min_qty': gettext('Safety Threshold'),
      'hw_status': gettext('Stock Status'),
      'custody_asset': gettext('Asset'),
      'custody_holder': gettext('Holder'),
      'custody_status': gettext('Acceptance Status'),
      'custody_accepted_date': gettext('Accepted Date'),
      'custody_eula_version': gettext('EULA Version'),
      'custody_signature_provider': gettext('Signature Provider'),
      'custody_qms_reference': gettext('QMS Reference'),
      'custody_ip_address': gettext('IP Address'),
      'custody_created_date': gettext('Created Date')
    };
    
    const headers = selectedCols.map(col => headersMap[col] || col);
    
    const summaryCardInput = document.querySelector('input[name="include_summary_cards"]') as HTMLInputElement | null;
    const stylePresetSelect = document.querySelector('select[name="style_preset"]') as HTMLSelectElement | null;
    
    const includeSummary = summaryCardInput ? summaryCardInput.checked : false;
    const stylePreset = stylePresetSelect ? stylePresetSelect.value || 'default' : 'default';
    
    // Per-preset CSS overrides (single source mirrors templates/core/reports/polished_report.html).
    // Single-quoted to avoid nested template literals; the chosen preset is baked in here.
    const presetCss: Record<string, string> = {
      compact: '.container{border-radius:8px}.header{padding:0;border-top:3px solid #4f46e5}.header .brandline{display:none}.header h2{padding:16px 24px 0 24px;font-size:18px}.header p{padding:0 24px;font-size:12px;color:#57534e}.content{padding:18px 24px}.metric-card{padding:9px 12px}.metric-card .value{font-size:16px}.metrics-table{border-spacing:8px 0;margin-bottom:18px}th,td{padding:5px 9px;font-size:11px}tbody tr:nth-child(even) td{background:#fafaf9}.group-title{margin:16px 0 4px 0;padding:5px 10px;font-size:11px}',
      financial: '.header{background:#1c1917;color:#fff}.header .brandline{color:#a8a29e}.header p{color:#d6d3d1}.metric-card{background:#fafaf9;border:1px solid #e7e5e4;border-left:3px solid #1c1917}.metric-card .value{color:#1c1917;font-size:23px}td{font-variant-numeric:tabular-nums}th{background:#f5f5f4;color:#44403c;border-bottom:2px solid #d6d3d1}.group-title{background:#1c1917;color:#fff}',
      minimal: 'body{background:#fff;padding:16px}.container{border:none;border-radius:0;max-width:760px}.header{padding:0 0 14px 0;border-bottom:2px solid #4f46e5}.header .brandline{color:#4f46e5}.content{padding:22px 0}.metric-card{background:#fff;border:none;border-left:2px solid #e7e5e4;border-radius:0;padding:4px 14px}.metric-card .value{font-size:18px}th{background:none;border-bottom:2px solid #1c1917}td{border-bottom:1px solid #f5f5f4}.group-title{background:none;padding:0;color:#4f46e5;border-bottom:1px solid #e7e5e4}.footer{background:none}',
    };
    const activePresetCss = presetCss[stylePreset] || '.header{background:#4f46e5;color:#fff}.header .brandline{color:#c7d2fe}.header p{color:#e0e7ff}.metric-card{background:#eef2ff;border:1px solid #e0e7ff;border-left:3px solid #4f46e5}.metric-card .value{color:#4f46e5}.group-title{color:#4f46e5;border-left:3px solid #4f46e5;background:#eef2ff;padding-left:11px}';

    // Build the HTML template (Stone neutrals + single Indigo accent + Inter).
    const template = `{% load utility_tags %}
<html>
<head>
    <style>
        body { font-family: 'Inter Variable', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #1c1917; line-height: 1.5; background-color: #fafaf9; margin: 0; padding: 24px; }
        .container { max-width: 820px; margin: 0 auto; background: #ffffff; border: 1px solid #e7e5e4; border-radius: 14px; overflow: hidden; }
        .header { padding: 28px 32px; }
        .brandline { font-size: 11px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; }
        .header h2 { margin: 6px 0 0 0; font-size: 23px; font-weight: 700; letter-spacing: -0.4px; }
        .header p { margin: 6px 0 0 0; font-size: 13.5px; }
        .content { padding: 28px 32px; }
        .meta { font-size: 11px; color: #78716c; margin-bottom: 22px; }
        .metrics-table { width: 100%; border-collapse: separate; border-spacing: 12px 0; margin: 0 -12px 26px -12px; }
        .metric-card { background: #fafaf9; border: 1px solid #e7e5e4; border-radius: 10px; padding: 14px 16px; text-align: left; vertical-align: top; }
        .metric-card .label { font-size: 10.5px; color: #78716c; text-transform: uppercase; letter-spacing: 0.6px; font-weight: 700; }
        .metric-card .value { font-size: 21px; font-weight: 700; color: #1c1917; margin-top: 5px; }
        .group-title { font-size: 12px; font-weight: 700; color: #44403c; background: #f5f5f4; padding: 7px 12px; border-radius: 6px; margin: 24px 0 8px 0; text-transform: uppercase; letter-spacing: 0.5px; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
        th, td { text-align: left; border-bottom: 1px solid #e7e5e4; padding: 11px 14px; font-size: 13px; }
        th { color: #57534e; font-weight: 600; background: #fafaf9; font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px; }
        .footer { padding: 18px 32px; text-align: center; font-size: 11px; color: #78716c; border-top: 1px solid #e7e5e4; background: #fafaf9; }
        ${activePresetCss}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="brandline">ITAMbox</div>
            <h2>{{ report_name }}</h2>
            <p>{{ description|default:"${description}" }}</p>
        </div>
        <div class="content">
            <div class="meta">{{ generated_at|date:"Y-m-d H:i" }} UTC</div>
            ${includeSummary ? `
            {% if summary_cards %}
            <table class="metrics-table"><tr>
                {% for card in summary_cards %}
                <td class="metric-card"><div class="label">{{ card.label }}</div><div class="value">{{ card.value }}</div></td>
                {% endfor %}
            </tr></table>
            {% endif %}
            ` : ''}
            {% for group_name, group_rows in grouped_data.items %}
                {% if group_name != 'General' %}<div class="group-title">{{ group_name }}</div>{% endif %}
                <table>
                    <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
                    <tbody>
                        {% for row in group_rows %}
                        <tr>${selectedCols.map(col => `<td>{{ row|lookup:"${headersMap[col]}" }}</td>`).join('')}</tr>
                        {% empty %}
                        <tr><td colspan="${headers.length}" style="text-align:center; color:#78716c; padding:20px;">${gettext('No records found.')}</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            {% endfor %}
        </div>
        <div class="footer">${gettext('Generated by ITAMbox — IT Asset Management.')}</div>
    </div>
</body>
</html>`;
    return template;
  }

  function initReportTemplateForm() {
    const advancedModeCheckbox = document.querySelector('input[name="advanced_mode"]') as HTMLInputElement | null;
    const templateContentDiv = document.querySelector('textarea[name="template_content"]') as HTMLTextAreaElement | null;
    
    if (!advancedModeCheckbox || !templateContentDiv) return;
    
    const templateGroup = (templateContentDiv.closest('.mb-3') || templateContentDiv.closest('.form-group')) as HTMLElement | null;
    
    const visualControls: HTMLElement[] = [
      'included_columns',
      'include_summary_cards',
      'include_distribution_chart',
      'group_by_field',
      'style_preset'
    ].map(name => {
      const el = document.querySelector(`[name="${name}"]`) || document.querySelector(`input[name="${name}"]`);
      if (el) {
        return el.closest('.mb-3') || el.closest('.form-group') as HTMLElement | null;
      }
      return null;
    }).filter((el): el is HTMLElement => el !== null);

    const colsContainer = document.getElementById('div_id_included_columns');
    if (colsContainer) {
      visualControls.push(colsContainer);
      
      const formChecks = Array.from(colsContainer.querySelectorAll('.form-check')) as HTMLElement[];
      if (formChecks.length > 0) {
        // Hide standard inputs
        formChecks.forEach(el => el.style.display = 'none');
        
        // Read saved sequence from the json_script element (autoescaped by Django).
        const savedSeqEl = document.getElementById('report-template-saved-sequence');
        let savedSeq: string[] = [];
        if (savedSeqEl && savedSeqEl.textContent) {
          try {
            const parsed = JSON.parse(savedSeqEl.textContent);
            if (Array.isArray(parsed)) {
              savedSeq = parsed;
            }
          } catch {
            savedSeq = [];
          }
        }
        
        if (savedSeq.length > 0) {
          const formChecksMap: Record<string, HTMLElement> = {};
          formChecks.forEach(checkDiv => {
            const input = checkDiv.querySelector('input');
            if (input) {
              formChecksMap[input.value] = checkDiv;
            }
          });
          // Append in correct order
          savedSeq.forEach(val => {
            const checkDiv = formChecksMap[val];
            if (checkDiv) {
              colsContainer.appendChild(checkDiv);
              delete formChecksMap[val];
            }
          });
          // Append any remaining elements
          Object.values(formChecksMap).forEach(checkDiv => {
            colsContainer.appendChild(checkDiv);
          });
        }
        
        let managerWrapper = document.getElementById('visual-cols-manager-wrapper');
        if (!managerWrapper) {
          managerWrapper = document.createElement('div');
          managerWrapper.id = 'visual-cols-manager-wrapper';
          managerWrapper.className = 'visual-cols-manager mt-2 p-3 bg-body-secondary rounded-3 border';
          managerWrapper.style.minHeight = '150px';
          managerWrapper.innerHTML = `
              <div class="row">
                  <div class="col-md-6 mb-3 mb-md-0">
                      <div class="text-secondary small fw-bold mb-2 uppercase text-uppercase d-flex align-items-center">
                          <i class="mdi mdi-sort me-1 text-primary"></i>
                          ${gettext('Selected Sequence (Orderable)')}
                      </div>
                      <div id="active-cols-list" class="d-flex flex-column gap-2 p-2 bg-body rounded border" style="min-height: 120px; max-height: 280px; overflow-y: auto;">
                          <span class="text-muted small text-center my-auto py-3 italic">${gettext('No columns selected. Click available columns to add them.')}</span>
                      </div>
                  </div>
                  <div class="col-md-6">
                      <div class="text-secondary small fw-bold mb-2 uppercase text-uppercase">
                          ${gettext('Available Columns')}
                      </div>
                      <div id="available-cols-list" class="d-flex flex-wrap gap-2 p-2 bg-body rounded border" style="min-height: 120px; align-content: flex-start; max-height: 280px; overflow-y: auto;">
                      </div>
                  </div>
              </div>
          `;
          colsContainer.appendChild(managerWrapper);
        }
        
        const activeList = managerWrapper.querySelector('#active-cols-list') as HTMLElement;
        const availableList = managerWrapper.querySelector('#available-cols-list') as HTMLElement;
        const reportTypeSelect = document.querySelector('select[name="report_type"]') as HTMLSelectElement | null;
        
        const columnsByReportType: Record<string, string[]> = {
          'asset_summary': [
            'asset_tag', 'name', 'manufacturer', 'model', 'serial_number',
            'status', 'location', 'assigned_to', 'purchase_cost',
            'purchase_date', 'warranty_months'
          ],
          'license_utilization': [
            'license_name', 'software', 'seats', 'assigned_seats',
            'available_seats', 'utilization_rate'
          ],
          'subscription_renewals': [
            'subscription_name', 'provider', 'billing_cycle', 'cost', 'end_date'
          ],
          'asset_maintenance': [
            'maintenance_title', 'maintenance_asset', 'maintenance_type',
            'maintenance_status', 'maintenance_cost', 'maintenance_start_date',
            'maintenance_completion_date', 'maintenance_downtime'
          ],
          'asset_depreciation': [
            'asset_tag', 'name', 'purchase_cost', 'salvage_value', 'depreciation_months', 'current_value'
          ],
          'software_inventory': [
            'software_name', 'manufacturer', 'version', 'category', 'license_type', 'installed_count', 'license_count'
          ],
          'contract_renewals': ['contract_number', 'contract_name', 'contract_type', 'contract_status', 'contract_supplier', 'contract_start_date', 'contract_end_date', 'contract_renewal_date', 'contract_days_until_expiry', 'contract_cost', 'contract_billing_cycle', 'contract_auto_renew', 'contract_covered_assets', 'contract_sla_response_time', 'contract_sla_resolution_time', 'contract_coverage_hours'],
          'warranty_expiration': ['warranty_asset', 'warranty_type', 'warranty_provider', 'warranty_start_date', 'warranty_end_date', 'warranty_days_remaining', 'warranty_status', 'warranty_cost', 'warranty_reference'],
          'asset_disposal_eol': ['disposal_asset', 'disposal_date', 'disposal_method', 'disposal_sanitization_method', 'disposal_sanitization_certificate', 'disposal_sanitized_by', 'disposal_recipient', 'disposal_proceeds', 'disposal_weee_compliant', 'disposal_notes'],
          'hardware_inventory': ['hw_item_type', 'hw_name', 'hw_manufacturer', 'hw_category', 'hw_part_number', 'hw_total_stock', 'hw_available', 'hw_min_qty', 'hw_status'],
          'custody_compliance': ['custody_asset', 'custody_holder', 'custody_status', 'custody_accepted_date', 'custody_eula_version', 'custody_signature_provider', 'custody_qms_reference', 'custody_ip_address', 'custody_created_date']
        };
        
        function renderVisualColumns() {
          activeList.innerHTML = '';
          availableList.innerHTML = '';
          
          const selectedType = reportTypeSelect ? reportTypeSelect.value : 'asset_summary';
          const validCols = columnsByReportType[selectedType] || [];
          
          const currentChecks = Array.from(colsContainer!.querySelectorAll('.form-check')) as HTMLElement[];
          let activeCount = 0;
          
          currentChecks.forEach((checkDiv) => {
            const input = checkDiv.querySelector('input') as HTMLInputElement | null;
            const label = checkDiv.querySelector('label') as HTMLElement | null;
            if (!input) return;
            
            const val = input.value;
            if (!validCols.includes(val)) {
              input.checked = false;
              return;
            }
            
            const isChecked = input.checked;
            const labelText = label ? label.textContent || val : val;
            
            if (isChecked) {
              activeCount++;
              const activeBadge = document.createElement('div');
              activeBadge.className = 'd-flex align-items-center justify-content-between p-2 bg-primary-lt rounded-2 border border-primary-subtle';
              activeBadge.style.transition = 'all 0.15s ease';
              activeBadge.style.cursor = 'default';
              activeBadge.innerHTML = `
                  <div class="d-flex align-items-center">
                      <span class="badge bg-primary text-primary-fg me-2" style="font-size: 10px;">${activeCount}</span>
                      <span class="small fw-semibold text-primary">${labelText.trim()}</span>
                  </div>
                  <div class="d-flex align-items-center gap-1">
                      <button type="button" class="btn btn-sm btn-icon btn-outline-primary py-0 px-1 border-0 btn-move-up" title="${gettext('Move Up')}">
                          <svg xmlns="http://www.w3.org/2000/svg" class="icon" width="14" height="14" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 5l0 14" /><path d="M18 11l-6 -6" /><path d="M6 11l6 -6" /></svg>
                      </button>
                      <button type="button" class="btn btn-sm btn-icon btn-outline-primary py-0 px-1 border-0 btn-move-down" title="${gettext('Move Down')}">
                          <svg xmlns="http://www.w3.org/2000/svg" class="icon" width="14" height="14" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 5l0 14" /><path d="M18 13l-6 6" /><path d="M6 13l6 6" /></svg>
                      </button>
                      <button type="button" class="btn btn-sm btn-icon btn-outline-danger py-0 px-1 border-0 btn-remove-col" title="${gettext('Remove')}">
                          <svg xmlns="http://www.w3.org/2000/svg" class="icon" width="14" height="14" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M18 6l-12 12" /><path d="M6 6l12 12" /></svg>
                      </button>
                  </div>
              `;
              
              activeBadge.querySelector('.btn-remove-col')!.addEventListener('click', function() {
                input.checked = false;
                colsContainer!.appendChild(checkDiv);
                renderVisualColumns();
              });
              
              activeBadge.querySelector('.btn-move-up')!.addEventListener('click', function() {
                const prev = checkDiv.previousElementSibling;
                if (prev && prev.classList.contains('form-check')) {
                  colsContainer!.insertBefore(checkDiv, prev);
                  renderVisualColumns();
                }
              });
              
              activeBadge.querySelector('.btn-move-down')!.addEventListener('click', function() {
                const next = checkDiv.nextElementSibling;
                if (next && next.classList.contains('form-check')) {
                  colsContainer!.insertBefore(checkDiv, next.nextSibling);
                  renderVisualColumns();
                }
              });
              
              activeList.appendChild(activeBadge);
            } else {
              const availPill = document.createElement('button');
              availPill.type = 'button';
              availPill.className = 'btn btn-sm btn-outline-secondary d-flex align-items-center py-1 px-2 text-start';
              availPill.style.borderRadius = '20px';
              availPill.style.fontSize = '11px';
              availPill.style.fontWeight = '500';
              availPill.innerHTML = `
                  <i class="mdi mdi-plus me-1"></i>
                  ${labelText.trim()}
              `;
              
              availPill.addEventListener('click', function() {
                input.checked = true;
                colsContainer!.appendChild(checkDiv);
                renderVisualColumns();
              });
              
              availableList.appendChild(availPill);
            }
          });
          
          if (activeCount === 0) {
            activeList.innerHTML = '<span class="text-muted small text-center my-auto py-3 italic">' + gettext('No columns selected. Click available columns to add them.') + '</span>';
          }
        }
        
        renderVisualColumns();
        
        if (reportTypeSelect) {
          reportTypeSelect.addEventListener('change', function() {
            const selectedType = reportTypeSelect.value;
            const validCols = columnsByReportType[selectedType] || [];
            const checkboxes = colsContainer!.querySelectorAll('input[name="included_columns"]') as NodeListOf<HTMLInputElement>;
            checkboxes.forEach(cb => {
              if (!validCols.includes(cb.value)) {
                cb.checked = false;
              }
            });
            renderVisualColumns();
          });
        }
      }
    }

    // Create the "Re-generate" button if it doesn't already exist
    const regenBtnId = 'btn-regen-from-visual';
    let regenBtn: HTMLElement | null = document.getElementById(regenBtnId);
    if (!regenBtn) {
      const newBtn = document.createElement('button');
      newBtn.id = regenBtnId;
      newBtn.type = 'button';
      newBtn.className = 'btn btn-sm btn-outline-primary mb-2';
      newBtn.style.fontSize = '12px';
      newBtn.innerHTML = '<i class="mdi mdi-refresh"></i> ' + gettext('Bootstrap from Visual Builder');
      regenBtn = newBtn;
      
      if (templateGroup && templateContentDiv) {
        templateGroup.insertBefore(regenBtn, templateContentDiv);
      }
      
      regenBtn.addEventListener('click', function(e) {
        e.preventDefault();
        if (confirm(gettext('Are you sure you want to overwrite the current custom template with the selections from the Visual Builder?'))) {
          if (templateContentDiv) {
            templateContentDiv.value = generateJinja2FromForm();
          }
        }
      });
    }
    
    function toggleFields() {
      const isAdvanced = advancedModeCheckbox!.checked;
      if (isAdvanced) {
        if (templateGroup) templateGroup.style.display = 'block';
        visualControls.forEach(el => el.style.display = 'none');
        
        if (templateContentDiv!.value.trim() === '') {
          templateContentDiv!.value = generateJinja2FromForm();
        }
      } else {
        if (templateGroup) templateGroup.style.display = 'none';
        visualControls.forEach(el => el.style.display = 'block');
      }
    }
    
    advancedModeCheckbox.addEventListener('change', toggleFields);
    toggleFields(); // initial run

    // Live Preview Button Setup
    const submitBtn = document.querySelector('input[name="submit"]') || document.querySelector('button[type="submit"]') || document.querySelector('.btn-primary');
    const existingPreviewBtn = document.getElementById('btn-preview-report');
    
    if (submitBtn && !existingPreviewBtn) {
      const previewBtn = document.createElement('button');
      previewBtn.type = 'button';
      previewBtn.id = 'btn-preview-report';
      previewBtn.className = 'btn btn-outline-info ms-2';
      previewBtn.style.borderRadius = '6px';
      previewBtn.style.fontWeight = '600';
      previewBtn.innerHTML = '<i class="mdi mdi-eye-outline me-1"></i> ' + gettext('Preview Report');
      
      submitBtn.parentNode!.insertBefore(previewBtn, submitBtn.nextSibling);
      
      previewBtn.addEventListener('click', function(e) {
        e.preventDefault();
        openReportPreviewModal();
      });
    }
  }

  function openReportPreviewModal() {
    const spinner = document.getElementById('previewSpinner');
    const frame = document.getElementById('previewFrame') as HTMLIFrameElement | null;
    
    if (spinner) {
      spinner.classList.remove('d-none');
      spinner.classList.add('d-flex');
    }
    if (frame) {
      frame.style.display = 'none';
      frame.srcdoc = '';
    }

    // Initialize and trigger Bootstrap Modal
    const modalEl = document.getElementById('previewModal');
    if (modalEl) {
      const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
      modalInstance.show();
    }

    // Gather form data
    const contentTextarea = document.querySelector('textarea[name="template_content"]');
    if (!contentTextarea) return;
    const formEl = contentTextarea.closest('form');
    if (!formEl) return;
    const formData = new FormData(formEl);

    // Fetch rendered HTML from view. The URL comes from the modal's
    // data-preview-url (Django {% url %}) so it survives URL-prefix changes
    // (the hardcoded '/reports/templates/preview/' was missing the 'extras/'
    // mount prefix -> 404). Fall back to the canonical path.
    const previewUrl = (modalEl && modalEl.getAttribute('data-preview-url')) || '/extras/reports/templates/preview/';
    fetch(previewUrl, {
      method: 'POST',
      body: formData,
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(response => {
      if (!response.ok) {
        return response.text().then(text => {
          throw new Error(text || gettext('Template rendering failed.'));
        });
      }
      return response.text();
    })
    .then(html => {
      if (spinner) {
        spinner.classList.add('d-none');
        spinner.classList.remove('d-flex');
      }
      if (frame) {
        frame.style.display = 'block';
        frame.srcdoc = html;
      }
    })
    .catch(error => {
      console.error('Error generating preview:', error);
      if (spinner) {
        spinner.classList.add('d-none');
        spinner.classList.remove('d-flex');
      }
      if (frame) {
        frame.style.display = 'block';
        const cleanErr = error.message;
        if (cleanErr.includes('Template Render Error:')) {
          frame.srcdoc = cleanErr;
        } else {
          frame.srcdoc = `<div style="padding: 20px; font-family: sans-serif; color: #721c24; background-color: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px;">
              <h4 style="margin-top: 0;">${gettext('Preview Render Error')}</h4>
              <pre style="white-space: pre-wrap; font-family: monospace;">${cleanErr}</pre>
          </div>`;
        }
      }
    });
  }
  
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initReportTemplateForm);
  } else {
    initReportTemplateForm();
  }
  document.addEventListener('htmx:afterSwap', initReportTemplateForm);
})();
