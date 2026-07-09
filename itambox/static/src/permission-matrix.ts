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
        // Capture desired state before dispatching (see row-toggle note below).
        const desired = this.checked;
        const action = this.getAttribute('data-action');
        const targetClass = `.${action}-checkbox input[type="checkbox"]`;
        const targetCheckboxes = document.querySelectorAll(targetClass);
        targetCheckboxes.forEach(cb => {
          (cb as HTMLInputElement).checked = desired;
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
        // Capture the desired state up-front. Dispatching 'change' on each checkbox
        // re-runs updateToggleState, which flips THIS toggle's own .checked mid-loop
        // (not all boxes are checked yet) — re-reading this.checked would then leave
        // every box after the first unchecked. Use the captured value instead.
        const desired = this.checked;
        checkboxes.forEach(cb => {
          cb.checked = desired;
          cb.dispatchEvent(new Event('change', { bubbles: true }));
        });
      });

      checkboxes.forEach(cb => {
        cb.addEventListener('change', updateToggleState);
      });
    });

    // 4. Preset picker
    // A preset pre-checks the matrix client-side from a server-supplied map of
    // preset -> matrix field names (perm_<key>_<action>). This is a convenience
    // only: the checkboxes are the source of truth and the server's escalation
    // guard re-validates the final grant on submit, so a preset can never grant
    // a permission the user could not have checked by hand.
    const presetPicker = document.querySelector('[data-role-preset-picker]') as HTMLSelectElement | null;
    if (presetPicker && !(presetPicker as any)._matrix_init) {
      (presetPicker as any)._matrix_init = true;

      let presetMap: Record<string, string[]> = {};
      const mapEl = document.getElementById('role-preset-field-map');
      if (mapEl && mapEl.textContent) {
        try {
          presetMap = JSON.parse(mapEl.textContent) as Record<string, string[]>;
        } catch (_e) {
          presetMap = {};
        }
      }

      presetPicker.addEventListener('change', function (this: HTMLSelectElement) {
        const fields = presetMap[this.value];
        if (!fields) return;
        const wanted = new Set(fields);
        // Only touch matrix checkboxes (those inside the *-checkbox cells), never
        // the row/column toggles or the custom-perm / provider-capability boxes.
        const matrixBoxes = document.querySelectorAll(
          '.read-checkbox input[type="checkbox"], .create-checkbox input[type="checkbox"], ' +
          '.edit-checkbox input[type="checkbox"], .delete-checkbox input[type="checkbox"]'
        ) as NodeListOf<HTMLInputElement>;
        matrixBoxes.forEach(cb => {
          cb.checked = wanted.has(cb.getAttribute('name') || '');
          // Notify row/column toggle listeners so their state stays in sync.
          cb.dispatchEvent(new Event('change', { bubbles: true }));
        });
      });
    }

    // 6. Container chooser (role create) — tenant vs provider.
    // On the plain /roles/add/ page the user picks a container to set the role's scope.
    // Enforce mutual exclusivity (picking one clears the other) and reveal the provider-
    // capabilities section only when a provider is chosen. Server-side clean() is the source
    // of truth (requires exactly one container and derives scope); this is UX only.
    const chooser = document.getElementById('role-container-chooser');
    if (chooser && !(chooser as any)._matrix_init) {
      (chooser as any)._matrix_init = true;
      const tenantSel = chooser.querySelector('[name="tenant"]') as HTMLSelectElement | null;
      const providerSel = chooser.querySelector('[name="provider"]') as HTMLSelectElement | null;
      const capsSection = document.getElementById('provider-capabilities-section');

      const sync = (changed: 'tenant' | 'provider' | null) => {
        if (changed === 'provider' && providerSel && providerSel.value && tenantSel) {
          tenantSel.value = '';
        }
        if (changed === 'tenant' && tenantSel && tenantSel.value && providerSel) {
          providerSel.value = '';
        }
        const providerChosen = !!(providerSel && providerSel.value);
        if (capsSection) capsSection.style.display = providerChosen ? '' : 'none';
      };

      if (tenantSel) tenantSel.addEventListener('change', () => sync('tenant'));
      if (providerSel) providerSel.addEventListener('change', () => sync('provider'));
      // Initial pass: keep caps visibility correct after a validation-error re-render, where
      // the previously chosen container is repopulated from POST data.
      sync(null);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPermissionMatrix);
  } else {
    initPermissionMatrix();
  }
  document.body.addEventListener('htmx:afterSettle', initPermissionMatrix);
})();
