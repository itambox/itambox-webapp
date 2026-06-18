/**
 * ITAMbox Toast Messages — Bootstrap toast initialization and HTMX lifecycle.
 *
 * Handles:
 *  - Initial page-load toast rendering (#django-messages container)
 *  - HTMX OOB-targeted toast swaps
 *  - Inline toast elements inside swapped content
 *  - Custom showMessage event for dynamic toast creation
 *  - refreshCurrentPage() utility for post-modal page refresh
 *  - Custom event listeners (closeModalEvent, assetListUpdated, kitListUpdated)
 */
(function () {
  function initToastsInContainer(container: HTMLElement): void {
    const toasts = container.querySelectorAll<HTMLElement>('.toast:not(.initialized)');
    toasts.forEach(function (toastEl) {
      toastEl.classList.add('initialized');
      const toast = new bootstrap.Toast(toastEl);
      toast.show();
      toastEl.addEventListener('hidden.bs.toast', function () {
        toastEl.remove();
      });
    });
  }

  function initTooltips(container: HTMLElement | Document = document): void {
    const tooltipTriggerList = Array.from(container.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.forEach(function (el) {
      bootstrap.Tooltip.getOrCreateInstance(el);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    const container = document.getElementById('django-messages');
    if (container) initToastsInContainer(container);
    initTooltips();
  });

  document.body.addEventListener('htmx:beforeSwap', function () {
    // Scan for and remove active/lingering tooltip DOM nodes to prevent orphans
    const activeTooltips = document.querySelectorAll('.tooltip');
    activeTooltips.forEach(function (el) {
      el.remove();
    });
  });

  document.body.addEventListener('htmx:afterSwap', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    
    // Always check for and initialize any new toasts in #django-messages container
    const container = document.getElementById('django-messages');
    if (container) {
      initToastsInContainer(container);
    }
    
    if (detail.elt) {
      initTooltips(detail.elt as HTMLElement);
    }
  });

  window.refreshCurrentPage = function () {
    htmx.ajax('GET', window.location.pathname + window.location.search, {
      target: '#page-content-wrapper',
      swap: 'innerHTML',
    });
  };

  document.body.addEventListener('closeModalEvent', function () {
    const openModalEl = document.querySelector<HTMLElement>('.modal.show');
    if (openModalEl) {
      const modalInstance = bootstrap.Modal.getInstance(openModalEl) || new bootstrap.Modal(openModalEl);
      
      // Defer page refresh until the modal's transition and backdrop cleanup are fully done
      openModalEl.addEventListener('hidden.bs.modal', function () {
        window.refreshCurrentPage?.();
      }, { once: true });

      modalInstance.hide();
    } else {
      // Fallback if no modal is visible
      window.refreshCurrentPage?.();
    }
  });

  document.body.addEventListener('assetListUpdated', function () {
    window.refreshCurrentPage?.();
  });

  document.body.addEventListener('kitListUpdated', function () {
    window.refreshCurrentPage?.();
  });

  document.body.addEventListener('showMessage', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const message = detail.value ? detail.value.message : detail.message || '';
    const level = detail.value ? detail.value.level : detail.level || 'info';
    if (!message) return;

    const container = document.getElementById('django-messages');
    if (!container) return;

    const toastEl = document.createElement('div');
    const bgClass = level === 'success' ? 'bg-success' : level === 'danger' ? 'bg-danger' : 'bg-primary';
    toastEl.className = 'toast align-items-center text-white ' + bgClass + ' border-0';
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');

    // Build the toast DOM explicitly and inject the message via textContent.
    // `message` originates from server-side HX-Trigger payloads that frequently
    // embed user-controlled data (object names, e.g. an asset named
    // "<img src=x onerror=...>"). Using innerHTML here would be a DOM-XSS sink;
    // textContent neutralises any markup. Only the static chrome is markup.
    const flex = document.createElement('div');
    flex.className = 'd-flex';
    const body = document.createElement('div');
    body.className = 'toast-body';
    body.textContent = message;
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn-close btn-close-white me-2 m-auto';
    closeBtn.setAttribute('data-bs-dismiss', 'toast');
    closeBtn.setAttribute('aria-label', 'Close');
    flex.appendChild(body);
    flex.appendChild(closeBtn);
    toastEl.appendChild(flex);
    container.appendChild(toastEl);

    const toast = new bootstrap.Toast(toastEl);
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', function () {
      toastEl.remove();
    });
  });
})();
