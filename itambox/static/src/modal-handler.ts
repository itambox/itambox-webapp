/**
 * ITAMbox — HTMX Modal Auto-Show Handler.
 *
 * When HTMX loads modal HTML into #modal-placeholder, this module
 * automatically detects the new .modal element and shows it via
 * Bootstrap's Modal API.
 *
 * This replaces the old pattern of inline hx-on::after-request JS
 * (which required 'unsafe-eval' in CSP) with a clean event listener.
 */
(function () {
  document.body.addEventListener('htmx:afterSettle', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    if (!detail || !detail.target) return;

    const target = detail.target as HTMLElement;
    if (target.id !== 'modal-placeholder') return;

    // Find any .modal inside the placeholder and show it
    const modals = target.querySelectorAll<HTMLElement>('.modal');
    modals.forEach(function (modal) {
      if (modal.classList.contains('show')) return;
      try {
        const inst = bootstrap.Modal.getOrCreateInstance(modal);
        if (!inst._isShown) {
          inst.show();
        }
      } catch (_e) {
        console.warn('ITAMbox modal auto-show failed:', _e);
      }
    });
  });
})();
