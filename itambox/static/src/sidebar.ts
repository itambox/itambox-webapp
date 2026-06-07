/**
 * ITAMbox Sidebar — Active state tracking with URL matching.
 *
 * Updates .nav-item.active, .nav-link.active, .dropdown-item.active
 * based on the current page URL. Handles HTMX navigation (afterSettle)
 * and browser popstate.
 */
(function () {
  function updateSidebarActiveState(): void {
    let currentPath = window.location.pathname;

    // Custom normalization for inventory sub-paths that don't match folder naming conventions
    if (currentPath.startsWith('/inventory/component-allocations') || currentPath.startsWith('/inventory/component-stocks')) {
      currentPath = '/inventory/components/';
    } else if (currentPath.startsWith('/inventory/accessory-stocks')) {
      currentPath = '/inventory/accessories/';
    } else if (currentPath.startsWith('/inventory/consumable-stocks')) {
      currentPath = '/inventory/consumables/';
    }

    const sidebar = document.getElementById('sidebar-menu');
    if (!sidebar) return;

    sidebar.querySelectorAll('.nav-item.active, .nav-link.active, .nav-link.show, .dropdown-item.active, .dropdown-menu.show').forEach(
      function (el: Element) {
        el.classList.remove('active');
        if (el.classList.contains('dropdown-menu') || el.classList.contains('nav-link')) {
          el.classList.remove('show');
        }
        if (el.classList.contains('nav-link')) {
          el.setAttribute('aria-expanded', 'false');
        }
      },
    );

    let bestMatch: Element | null = null;
    let bestMatchLength = 0;

    sidebar.querySelectorAll<HTMLAnchorElement>('.dropdown-item a[href], .nav-item > .nav-link[href]').forEach(
      function (link) {
        const href = link.getAttribute('href');
        if (!href || href === '#') return;

        let resolved: string;
        try {
          resolved = new URL(href, window.location.origin).pathname;
        } catch (_e) {
          return;
        }

        let normalizedResolved = resolved;
        if (normalizedResolved.endsWith('/') && normalizedResolved !== '/') {
          normalizedResolved = normalizedResolved.slice(0, -1);
        }

        if (currentPath === resolved || (normalizedResolved !== '/' && currentPath.indexOf(normalizedResolved + '/') === 0)) {
          if (resolved.length > bestMatchLength) {
            bestMatch = link;
            bestMatchLength = resolved.length;
          }
        }
      },
    );

    if (bestMatch) {
      const dropdownItem = bestMatch.closest('.dropdown-item');
      if (dropdownItem) dropdownItem.classList.add('active');

      const navItem = bestMatch.closest('.nav-item');
      if (navItem) {
        navItem.classList.add('active');
        const navLink = navItem.querySelector<HTMLElement>(':scope > .nav-link');
        if (navLink) {
          navLink.classList.add('active');
          navLink.classList.add('show');
          navLink.setAttribute('aria-expanded', 'true');
        }
        const dropdownMenu = navItem.querySelector<HTMLElement>(':scope > .dropdown-menu');
        if (dropdownMenu) dropdownMenu.classList.add('show');
      }
    } else if (currentPath === '/' || currentPath === '') {
      const homeItem = sidebar.querySelector('.nav-item:first-child');
      if (homeItem) homeItem.classList.add('active');
    }
  }

  document.addEventListener('DOMContentLoaded', updateSidebarActiveState);
  document.body.addEventListener('htmx:afterSettle', function (evt: Event) {
    updateSidebarActiveState();

    // Automatically hide mobile offcanvas menu only after page navigation (swap target is page-content-wrapper)
    const detail = (evt as CustomEvent).detail;
    const target = detail && (detail.target as HTMLElement);
    if (target && target.id === 'page-content-wrapper') {
      const sidebar = document.getElementById('sidebar-menu');
      if (sidebar && typeof bootstrap !== 'undefined' && bootstrap.Offcanvas) {
        const offcanvasInstance = bootstrap.Offcanvas.getInstance(sidebar);
        if (offcanvasInstance) {
          offcanvasInstance.hide();
        }
      }
    }
  });
  window.addEventListener('popstate', updateSidebarActiveState);
})();
