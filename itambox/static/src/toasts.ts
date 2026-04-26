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
    const toasts = container.querySelectorAll<HTMLElement>('.toast');
    toasts.forEach(function (toastEl) {
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
    if (detail.target && detail.target.id === 'django-messages') {
      initToastsInContainer(detail.target as HTMLElement);
      return;
    }
    if (detail.elt) {
      initToastsInContainer(detail.elt as HTMLElement);
      initTooltips(detail.elt as HTMLElement);
    }
  });

  (window as unknown as Record<string, unknown>).refreshCurrentPage = function () {
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
        (window as unknown as Record<string, unknown>).refreshCurrentPage();
      }, { once: true });
      
      modalInstance.hide();
    } else {
      // Fallback if no modal is visible
      (window as unknown as Record<string, unknown>).refreshCurrentPage();
    }
  });

  document.body.addEventListener('assetListUpdated', function () {
    (window as unknown as Record<string, unknown>).refreshCurrentPage();
  });

  document.body.addEventListener('kitListUpdated', function () {
    (window as unknown as Record<string, unknown>).refreshCurrentPage();
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
    toastEl.innerHTML =
      '<div class="d-flex"><div class="toast-body">' +
      message +
      '</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>';
    container.appendChild(toastEl);

    const toast = new bootstrap.Toast(toastEl);
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', function () {
      toastEl.remove();
    });
  });
})();
