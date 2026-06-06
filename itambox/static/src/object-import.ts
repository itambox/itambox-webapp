/**
 * ITAMbox — Object Import Form Formatter.
 */
(function () {
  function initImportForm() {
    const form = document.getElementById('import-form');
    if (!form) return;

    const activeTabId = form.getAttribute('data-active-tab-id');
    const importTextId = form.getAttribute('data-import-text-id');
    const fieldNamesRaw = form.getAttribute('data-field-names') || '[]';
    const fieldNames: string[] = JSON.parse(fieldNamesRaw);

    const activeTabInput = document.getElementById(activeTabId || '') as HTMLInputElement | null;
    const formatCsvRadio = document.getElementById('format-csv') as HTMLInputElement | null;
    const formatYamlRadio = document.getElementById('format-yaml') as HTMLInputElement | null;
    const delimiterContainer = document.getElementById('delimiter-container');
    const importTextarea = document.getElementById(importTextId || '') as HTMLTextAreaElement | null;

    if (!formatCsvRadio || !formatYamlRadio || !delimiterContainer || !importTextarea || !activeTabInput) return;

    const csvPlaceholder = fieldNames.join(',') + '\n' + fieldNames.map(() => 'value').join(',');
    const yamlPlaceholder = '- ' + fieldNames.map((f, i) => (i === 0 ? '' : '  ') + f + ': "value"').join('\n');

    function updatePlaceholdersAndInputs() {
      const selectedFormat = formatCsvRadio!.checked ? 'csv' : 'yaml';
      
      if (selectedFormat === 'csv') {
        delimiterContainer!.classList.remove('d-none');
        importTextarea!.placeholder = csvPlaceholder;
      } else {
        delimiterContainer!.classList.add('d-none');
        importTextarea!.placeholder = yamlPlaceholder;
      }
    }

    formatCsvRadio.addEventListener('change', updatePlaceholdersAndInputs);
    formatYamlRadio.addEventListener('change', updatePlaceholdersAndInputs);

    const tabUploadBtn = document.getElementById('btn-tab-upload');
    const tabEditorBtn = document.getElementById('btn-tab-editor');

    if (tabUploadBtn) {
      tabUploadBtn.addEventListener('shown.bs.tab', () => {
        activeTabInput.value = 'upload';
      });
      tabUploadBtn.addEventListener('click', () => {
        activeTabInput.value = 'upload';
      });
    }

    if (tabEditorBtn) {
      tabEditorBtn.addEventListener('shown.bs.tab', () => {
        activeTabInput.value = 'editor';
      });
      tabEditorBtn.addEventListener('click', () => {
        activeTabInput.value = 'editor';
      });
    }

    if (activeTabInput.value === 'editor' && tabEditorBtn) {
      try {
        const tabTrigger = (bootstrap as any).Tab.getOrCreateInstance(tabEditorBtn);
        tabTrigger.show();
      } catch (_e) {
        tabEditorBtn.click();
      }
    }

    updatePlaceholdersAndInputs();
  }

  document.addEventListener('DOMContentLoaded', initImportForm);
  document.body.addEventListener('htmx:afterSettle', initImportForm);
})();
