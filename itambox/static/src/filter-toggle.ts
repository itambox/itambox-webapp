/**
 * ITAMbox Filter Toggle — manages filter sidebar visibility.
 *
 * Exposes window.toggleFilters() and window.initFiltersToggle() for use
 * in object list pages and HTMX lifecycle hooks.
 */
(function () {
  function toggleFilters(): void {
    const mainCol = document.getElementById('object-list-main-col');
    const filterCol = document.getElementById('object-list-filter-col');
    const toggleBtn = document.getElementById('toggle-filters-btn');

    if (!filterCol || !mainCol) return;

    const isHidden = filterCol.classList.contains('d-none');
    if (isHidden) {
      filterCol.classList.remove('d-none');
      mainCol.classList.remove('col-md-12');
      mainCol.classList.add('col-md-9');
      if (toggleBtn) {
        toggleBtn.classList.add('active');
        toggleBtn.classList.remove('btn-outline-secondary');
        toggleBtn.classList.add('btn-outline-primary');
      }
      localStorage.setItem('itambox-show-filters', 'true');
    } else {
      filterCol.classList.add('d-none');
      mainCol.classList.remove('col-md-9');
      mainCol.classList.add('col-md-12');
      if (toggleBtn) {
        toggleBtn.classList.remove('active');
        toggleBtn.classList.remove('btn-outline-primary');
        toggleBtn.classList.add('btn-outline-secondary');
      }
      localStorage.setItem('itambox-show-filters', 'false');
    }
  }

  function initFiltersToggle(): void {
    const mainCol = document.getElementById('object-list-main-col');
    const filterCol = document.getElementById('object-list-filter-col');
    const toggleBtn = document.getElementById('toggle-filters-btn');

    if (!filterCol || !mainCol) return;

    const storedPreference = localStorage.getItem('itambox-show-filters');
    const isMobile = window.innerWidth < 768;

    let showFilters = true;
    if (storedPreference === 'false') {
      showFilters = false;
    } else if (storedPreference === 'true') {
      showFilters = true;
    } else if (isMobile) {
      showFilters = false;
    }

    if (showFilters) {
      filterCol.classList.remove('d-none');
      mainCol.classList.remove('col-md-12');
      mainCol.classList.add('col-md-9');
      if (toggleBtn) {
        toggleBtn.classList.add('active');
        toggleBtn.classList.remove('btn-outline-secondary');
        toggleBtn.classList.add('btn-outline-primary');
      }
    } else {
      filterCol.classList.add('d-none');
      mainCol.classList.remove('col-md-9');
      mainCol.classList.add('col-md-12');
      if (toggleBtn) {
        toggleBtn.classList.remove('active');
        toggleBtn.classList.remove('btn-outline-primary');
        toggleBtn.classList.add('btn-outline-secondary');
      }
    }
  }

  (window as unknown as Record<string, unknown>).toggleFilters = toggleFilters;
  (window as unknown as Record<string, unknown>).initFiltersToggle = initFiltersToggle;

  // Delegate click on #toggle-filters-btn at document level
  document.addEventListener('click', function (event) {
    const toggleBtn = (event.target as HTMLElement).closest('#toggle-filters-btn');
    if (toggleBtn) {
      event.preventDefault();
      toggleFilters();
    }
  });

  // Delegate click on .clear-search-btn to bypass strict CSP (which blocks inline onclick)
  document.addEventListener('click', function (event) {
    const clearBtn = (event.target as HTMLElement).closest('.clear-search-btn');
    if (clearBtn) {
      event.preventDefault();
      const form = clearBtn.closest('form');
      if (form) {
        const input = form.querySelector('input[name="q"]') as HTMLInputElement | null;
        if (input) {
          input.value = '';
          if (typeof form.requestSubmit === 'function') {
            form.requestSubmit();
          } else {
            form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
          }
        }
      }
    }
  });

  function scheduleFilterInit(): void {
    queueMicrotask(() => {
      initFiltersToggle();
    });
  }

  // Re-initialize after HTMX settles (DOM fully updated, OOB swaps complete)
  document.body.addEventListener('htmx:afterSettle', function (event: Event) {
    scheduleFilterInit();
  });

  // Initial call on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scheduleFilterInit);
  } else {
    scheduleFilterInit();
  }
})();
