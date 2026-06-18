/**
 * ITAMbox — Report Template Designer and Column Sequence Manager.
 */
(function () {
  function generateJinja2FromForm(): string {
    const descInput = document.querySelector('textarea[name="description"]') as HTMLTextAreaElement | null;
    const typeSelect = document.querySelector('select[name="report_type"]') as HTMLSelectElement | null;

    const description = descInput ? descInput.value || 'Visual inventory compilation.' : 'Visual inventory compilation.';
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
      } else {
        selectedCols = ['subscription_name', 'provider', 'billing_cycle', 'cost', 'end_date'];
      }
    }
    
    // Header display names map
    const headersMap: Record<string, string> = {
      'asset_tag': 'Asset Tag',
      'name': 'Asset Name',
      'manufacturer': 'Manufacturer',
      'model': 'Model',
      'serial_number': 'Serial Number',
      'status': 'Status Label',
      'location': 'Location',
      'assigned_to': 'Asset Holder',
      'purchase_cost': 'Purchase Cost',
      'purchase_date': 'Purchase Date',
      'warranty_months': 'Warranty (Months)',
      'license_name': 'License Name',
      'software': 'Software',
      'seats': 'Total Seats',
      'assigned_seats': 'Assigned Seats',
      'available_seats': 'Available Seats',
      'utilization_rate': 'Utilization Rate',
      'subscription_name': 'Subscription Name',
      'provider': 'Provider',
      'billing_cycle': 'Billing Cycle',
      'cost': 'Cost',
      'end_date': 'End Date',
      'salvage_value': 'Salvage Value',
      'depreciation_months': 'Depreciation Lifespan (Months)',
      'current_value': 'Depreciated Value',
      'software_name': 'Software Product',
      'installed_count': 'Installed Count',
      'license_count': 'License Count',
      'maintenance_title': 'Maintenance Title',
      'maintenance_asset': 'Asset',
      'maintenance_type': 'Type',
      'maintenance_status': 'Status',
      'maintenance_cost': 'Cost'
    };
    
    const headers = selectedCols.map(col => headersMap[col] || col);
    
    const summaryCardInput = document.querySelector('input[name="include_summary_cards"]') as HTMLInputElement | null;
    const stylePresetSelect = document.querySelector('select[name="style_preset"]') as HTMLSelectElement | null;
    
    const includeSummary = summaryCardInput ? summaryCardInput.checked : false;
    const stylePreset = stylePresetSelect ? stylePresetSelect.value || 'default' : 'default';
    
    const isCompact = stylePreset === 'compact';
    const isFinancial = stylePreset === 'financial';
    
    // Build the HTML template
    const template = `{% load utility_tags %}
<html>
<head>
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
            margin-top: 24px;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 16px;
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
        th, td {
            padding: ${isCompact ? '6px 8px' : '12px 14px'};
            font-size: ${isCompact ? '11px' : '13px'};
        }
        
        ${isFinancial ? `
        td:contains('$') {
            font-weight: 700;
            color: #0f766e;
        }
        ` : ''}
        
        .footer {
            background: #f8fafc;
            padding: 24px;
            text-align: center;
            font-size: 12px;
            color: #64748b;
            border-top: 1px solid #e2e8f0;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>{{ report_name }}</h2>
            <p>{{ description|default:"${description}" }}</p>
        </div>
        <div class="content">
            <div class="meta">
                GENERATED: {{ generated_at|date:"Y-m-d H:i:s" }} UTC | STYLE: ${stylePreset.toUpperCase()}
            </div>
            
            ${includeSummary ? `
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
            ` : ''}
            
            {% for group_name, group_rows in grouped_data.items %}
                {% if group_name != 'General' %}
                <div class="group-title">{{ group_name }}</div>
                {% endif %}
                <table>
                    <thead>
                        <tr>
                            ${headers.map(h => `<th>${h}</th>`).join('\n                            ')}
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in group_rows %}
                        <tr>
                            ${selectedCols.map(col => `<td>{{ row|lookup:"${headersMap[col]}" }}</td>`).join('\n                            ')}
                        </tr>
                        {% empty %}
                        <tr>
                            <td colspan="${headers.length}" style="text-align: center; color: #64748b; padding: 20px;">
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
        
        // Read saved sequence from report-template-metadata element
        const meta = document.getElementById('report-template-metadata');
        const savedSeqRaw = meta ? meta.getAttribute('data-saved-sequence') : null;
        const savedSeq: string[] = savedSeqRaw ? JSON.parse(savedSeqRaw) : [];
        
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
                          Selected Sequence (Orderable)
                      </div>
                      <div id="active-cols-list" class="d-flex flex-column gap-2 p-2 bg-body rounded border" style="min-height: 120px; max-height: 280px; overflow-y: auto;">
                          <span class="text-muted small text-center my-auto py-3 italic">No columns selected. Click available columns to add them.</span>
                      </div>
                  </div>
                  <div class="col-md-6">
                      <div class="text-secondary small fw-bold mb-2 uppercase text-uppercase">
                          Available Columns
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
          ]
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
                      <button type="button" class="btn btn-sm btn-icon btn-outline-primary py-0 px-1 border-0 btn-move-up" title="Move Up">
                          <svg xmlns="http://www.w3.org/2000/svg" class="icon" width="14" height="14" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 5l0 14" /><path d="M18 11l-6 -6" /><path d="M6 11l6 -6" /></svg>
                      </button>
                      <button type="button" class="btn btn-sm btn-icon btn-outline-primary py-0 px-1 border-0 btn-move-down" title="Move Down">
                          <svg xmlns="http://www.w3.org/2000/svg" class="icon" width="14" height="14" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 5l0 14" /><path d="M18 13l-6 6" /><path d="M6 13l6 6" /></svg>
                      </button>
                      <button type="button" class="btn btn-sm btn-icon btn-outline-danger py-0 px-1 border-0 btn-remove-col" title="Remove">
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
            activeList.innerHTML = '<span class="text-muted small text-center my-auto py-3 italic">No columns selected. Click available columns to add them.</span>';
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
      newBtn.innerHTML = '<i class="mdi mdi-refresh"></i> Bootstrap from Visual Builder';
      regenBtn = newBtn;
      
      if (templateGroup && templateContentDiv) {
        templateGroup.insertBefore(regenBtn, templateContentDiv);
      }
      
      regenBtn.addEventListener('click', function(e) {
        e.preventDefault();
        if (confirm('Are you sure you want to overwrite the current custom template with the selections from the Visual Builder?')) {
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
      previewBtn.innerHTML = '<i class="mdi mdi-eye-outline me-1"></i> Preview Report';
      
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

    // Fetch rendered HTML from view
    fetch('/reports/templates/preview/', {
      method: 'POST',
      body: formData,
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(response => {
      if (!response.ok) {
        return response.text().then(text => {
          throw new Error(text || 'Template rendering failed.');
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
              <h4 style="margin-top: 0;">Preview Render Error</h4>
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
