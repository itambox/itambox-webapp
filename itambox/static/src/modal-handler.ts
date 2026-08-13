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
  let pendingModalTrigger: HTMLElement | null = null;
  let pendingEscapeModal: HTMLElement | null = null;
  const openedModals = new WeakSet<HTMLElement>();

  // Bootstrap ignores Escape while a modal is opening because its internal
  // transition guard makes hide() a no-op. Capture the key before Bootstrap's
  // bubbling listener and finish the dismissal after the opening transition.
  document.body.addEventListener('show.bs.modal', function (evt: Event) {
    if (evt.target instanceof HTMLElement) openedModals.delete(evt.target);
  });

  document.addEventListener('keydown', function (evt: KeyboardEvent) {
    if (evt.key !== 'Escape') return;
    const modal = (evt.target as HTMLElement | null)?.closest<HTMLElement>('.modal.show');
    if (!modal) return;
    const instance = bootstrap.Modal.getInstance(modal);
    if (!instance) return;

    evt.preventDefault();
    evt.stopPropagation();
    if (openedModals.has(modal)) {
      instance.hide();
    } else {
      pendingEscapeModal = modal;
    }
  }, true);

  document.body.addEventListener('shown.bs.modal', function (evt: Event) {
    const modal = evt.target;
    if (!(modal instanceof HTMLElement)) return;
    openedModals.add(modal);
    if (modal === pendingEscapeModal) {
      pendingEscapeModal = null;
      bootstrap.Modal.getInstance(modal)?.hide();
    }
  });

  document.body.addEventListener('hidden.bs.modal', function (evt: Event) {
    if (evt.target instanceof HTMLElement) openedModals.delete(evt.target);
  });

  document.body.addEventListener('htmx:beforeRequest', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const target = detail?.target as HTMLElement | undefined;
    const requestElement = detail?.elt as HTMLElement | undefined;
    if (target?.id !== 'modal-placeholder' || !requestElement) return;

    const focused = requestElement.matches(':focus')
      ? requestElement
      : requestElement.querySelector<HTMLElement>(':focus');
    pendingModalTrigger = focused || requestElement;
  });

  // 1. HTMX Auto-Show and auto-cleanup listener
  document.body.addEventListener('htmx:afterSettle', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    if (!detail || !detail.target) return;

    const target = detail.target as HTMLElement;
    if (target.id !== 'modal-placeholder' || evt.target !== target) return;

    const triggerEl = pendingModalTrigger;
    pendingModalTrigger = null;
    const modals = target.querySelectorAll<HTMLElement>('.modal');
    modals.forEach(function (modal) {
      try {
        const inst = bootstrap.Modal.getOrCreateInstance(modal);

        // Remove focus from the modal before Bootstrap applies aria-hidden.
        modal.addEventListener('hide.bs.modal', function () {
          if (document.activeElement && modal.contains(document.activeElement)) {
            (document.activeElement as HTMLElement).blur();
          }
        });

        // Restore focus and clean up only after the modal and backdrop are fully hidden.
        modal.addEventListener('hidden.bs.modal', function () {
          if (triggerEl && triggerEl.isConnected && typeof triggerEl.focus === 'function') {
            triggerEl.focus();
          }
          modal.remove();
        }, { once: true });

        inst.show();
      } catch (_e) {
        console.warn('ITAMbox modal auto-show failed:', _e);
      }
    });
  });

  // 1b. Dismiss only the existing explicit Cancel-link contract. Generic
  // anchors may be legitimate modal content and must keep their navigation.
  document.body.addEventListener('htmx:beforeRequest', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const elt = detail?.elt as HTMLElement | undefined;
    if (!elt || !elt.matches('a[data-no-dirty-track="true"]') || detail?.boosted !== true) return;
    const modal = elt.closest('.modal') as HTMLElement | null;
    if (!modal) return;

    evt.preventDefault();
    try {
      bootstrap.Modal.getOrCreateInstance(modal).hide();
    } catch (_e) {
      console.warn('ITAMbox modal cancel-dismiss failed:', _e);
    }
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

    if (holderDiv) holderDiv.classList.toggle('d-none', !(targetType === 'holder' || targetType === 'assetholder'));
    if (locationDiv) locationDiv.classList.toggle('d-none', targetType !== 'location');
    if (assetDiv) assetDiv.classList.toggle('d-none', targetType !== 'asset');
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
