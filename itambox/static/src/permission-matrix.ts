/**
 * ITAMbox — Permission Matrix Toggles.
 *
 * Provides row, column, and group toggles for complex permission matrices.
 */
(function () {
  function initPermissionMatrix() {
    // 1. Column toggles
    const colToggles = document.querySelectorAll('.col-toggle');
    colToggles.forEach(toggle => {
      if ((toggle as any)._matrix_init) return;
      (toggle as any)._matrix_init = true;
      toggle.addEventListener('change', function (this: HTMLInputElement) {
        const action = this.getAttribute('data-action');
        const targetClass = `.${action}-checkbox input[type="checkbox"]`;
        const targetCheckboxes = document.querySelectorAll(targetClass);
        targetCheckboxes.forEach(cb => {
          (cb as HTMLInputElement).checked = this.checked;
          cb.dispatchEvent(new Event('change', { bubbles: true }));
        });
      });
    });

    // 2. Group toggles
    const groupToggleBtns = document.querySelectorAll('.toggle-group-btn');
    groupToggleBtns.forEach(btn => {
      if ((btn as any)._matrix_init) return;
      (btn as any)._matrix_init = true;
      btn.addEventListener('click', function (this: HTMLElement) {
        const groupName = this.getAttribute('data-group');
        const rowSelector = `.${groupName}-row`;
        const groupRows = document.querySelectorAll(rowSelector);
        
        let anyUnchecked = false;
        groupRows.forEach(row => {
          const checkboxes = row.querySelectorAll('td input[type="checkbox"]:not(.row-toggle-check)');
          checkboxes.forEach(cb => {
            if (!(cb as HTMLInputElement).checked) {
              anyUnchecked = true;
            }
          });
        });

        groupRows.forEach(row => {
          const checkboxes = row.querySelectorAll('td input[type="checkbox"]');
          checkboxes.forEach(cb => {
            (cb as HTMLInputElement).checked = anyUnchecked;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
          });
        });
      });
    });

    // 3. Row toggles
    const rowToggleChecks = document.querySelectorAll('.row-toggle-check') as NodeListOf<HTMLInputElement>;
    rowToggleChecks.forEach(toggle => {
      if ((toggle as any)._matrix_init) return;
      (toggle as any)._matrix_init = true;
      const row = toggle.closest('tr');
      if (!row) return;
      const checkboxes = row.querySelectorAll('td input[type="checkbox"]:not(.row-toggle-check)') as NodeListOf<HTMLInputElement>;
      
      const updateToggleState = () => {
        let allChecked = true;
        checkboxes.forEach(cb => {
          if (!cb.checked) allChecked = false;
        });
        toggle.checked = allChecked;
      };

      // Initialize state on load
      updateToggleState();

      toggle.addEventListener('change', function (this: HTMLInputElement) {
        checkboxes.forEach(cb => {
          cb.checked = this.checked;
          cb.dispatchEvent(new Event('change', { bubbles: true }));
        });
      });

      checkboxes.forEach(cb => {
        cb.addEventListener('change', updateToggleState);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPermissionMatrix);
  } else {
    initPermissionMatrix();
  }
  document.body.addEventListener('htmx:afterSettle', initPermissionMatrix);
})();
