/**
 * ITAMbox — HTMX Modal Auto-Show, Cleanup, Field Toggles, and Quick-Add Handler.
 *
 * Automatically:
 *  - Detects new .modal elements swapped into #modal-placeholder and shows them.
 *  - Adds a 'hidden.bs.modal' listener to automatically remove modals from the DOM.
 *  - Toggles target checkout form fields depending on the selected target_type.
 *  - Listens for 'quickAddSuccess' to dynamically insert and select options.
 */
(function () {
  // 1. HTMX Auto-Show and auto-cleanup listener
  document.body.addEventListener('htmx:afterSettle', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    if (!detail || !detail.target) return;

    const target = detail.target as HTMLElement;
    if (target.id !== 'modal-placeholder') return;

    const triggerEl = detail.elt as HTMLElement | null;
    const modals = target.querySelectorAll<HTMLElement>('.modal');
    modals.forEach(function (modal) {
      try {
        const inst = bootstrap.Modal.getOrCreateInstance(modal);
        
        // Restore focus to trigger element on hide to prevent aria-hidden focus warnings
        modal.addEventListener('hide.bs.modal', function () {
          if (triggerEl && typeof triggerEl.focus === 'function') {
            triggerEl.focus();
          } else if (document.activeElement && modal.contains(document.activeElement)) {
            (document.activeElement as HTMLElement).blur();
          }
        });

        // Clean up modal from DOM after it is hidden
        modal.addEventListener('hidden.bs.modal', function () {
          modal.remove();
        }, { once: true });
        
        inst.show();
      } catch (_e) {
        console.warn('ITAMbox modal auto-show failed:', _e);
      }
    });
  });

  // 2. Dynamic target fields toggling for checkout and request forms (Assets, Licenses, Subscriptions)
  function updateCheckoutFormFields(form: HTMLFormElement) {
    const targetTypeSelect = form.querySelector('select[name=target_type]') as HTMLSelectElement | null;
    if (!targetTypeSelect) return;
    const targetType = targetTypeSelect.value;

    const holderDiv = form.querySelector('#div_id_asset_holder, #div_id_assigned_holder, #div_id_assigned_user') as HTMLElement | null;
    const locationDiv = form.querySelector('#div_id_location, #div_id_assigned_location') as HTMLElement | null;
    
    // For requests, we have both #div_id_assigned_asset and #div_id_asset (which is the requested asset itself).
    // We only want to toggle the target #div_id_assigned_asset, not the requested asset.
    const isRequestForm = !!form.querySelector('[name=assigned_asset], [name=assigned_user], [name=assigned_location]');
    const assignedAssetDiv = form.querySelector('#div_id_assigned_asset') as HTMLElement | null;
    const assetDiv = isRequestForm ? assignedAssetDiv : (assignedAssetDiv || (form.querySelector('#div_id_asset_target, #div_id_asset') as HTMLElement | null));

    if (holderDiv) holderDiv.style.display = (targetType === 'holder' || targetType === 'assetholder') ? '' : 'none';
    if (locationDiv) locationDiv.style.display = (targetType === 'location') ? '' : 'none';
    if (assetDiv) assetDiv.style.display = (targetType === 'asset') ? '' : 'none';
  }

  function initCheckoutForms(root: HTMLElement | Document = document) {
    const selects = root.querySelectorAll('select[name=target_type]');
    selects.forEach((select) => {
      const form = select.closest('form');
      if (form) {
        updateCheckoutFormFields(form);
      }
    });
  }

  // Bind checkout form toggles
  document.addEventListener('DOMContentLoaded', () => initCheckoutForms());
  document.body.addEventListener('htmx:afterSettle', () => initCheckoutForms());
  document.body.addEventListener('shown.bs.modal', () => initCheckoutForms());

  document.body.addEventListener('change', (e) => {
    const target = e.target as HTMLSelectElement;
    if (target && target.name === 'target_type') {
      const form = target.closest('form');
      if (form) {
        updateCheckoutFormFields(form);
      }
    }
  });

  // 3. Quick-Add Success Event Listener (dispatched from server HX-Trigger header)
  document.body.addEventListener('quickAddSuccess', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    if (!detail) return;
    const { target_id, pk, value } = detail;
    const targetSelect = document.getElementById(target_id) as HTMLSelectElement | null;
    if (targetSelect) {
      let opt = targetSelect.querySelector(`option[value="${pk}"]`) as HTMLOptionElement | null;
      if (!opt) {
        opt = document.createElement('option');
        opt.value = pk;
        opt.textContent = value;
        targetSelect.insertBefore(opt, targetSelect.firstChild);
      }
      opt.selected = true;

      // Update TomSelect if instantiated
      if ((targetSelect as any).tomselect) {
        (targetSelect as any).tomselect.addOption({ value: pk, text: value });
        (targetSelect as any).tomselect.setValue(pk);
      } else {
        targetSelect.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }

    // Hide the quick-add modal
    const modal = document.getElementById('quick-add-modal');
    if (modal) {
      const inst = bootstrap.Modal.getInstance(modal);
      if (inst) inst.hide();
    }
  });
})();
