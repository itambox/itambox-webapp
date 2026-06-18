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

    // `bestMatch` is only assigned inside the forEach closure above; TS's
    // control-flow analysis narrows it to `never` here, so cast it back.
    const matchedLink = bestMatch as Element | null;
    if (matchedLink) {
      const dropdownItem = matchedLink.closest('.dropdown-item');
      if (dropdownItem) dropdownItem.classList.add('active');

      const navItem = matchedLink.closest('.nav-item');
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
    // Only react to actual page navigations (boosted swaps target
    // #page-content-wrapper). Partial swaps — e.g. the 30s notification
    // poll — must NOT reset the sidebar: updateSidebarActiveState()
    // collapses every menu the user has manually expanded.
    const detail = (evt as CustomEvent).detail;
    const target = detail && (detail.target as HTMLElement);
    if (!target || target.id !== 'page-content-wrapper') return;

    updateSidebarActiveState();

    // Automatically hide the mobile offcanvas menu after page navigation
    const sidebar = document.getElementById('sidebar-menu');
    if (sidebar && typeof bootstrap !== 'undefined' && bootstrap.Offcanvas) {
      const offcanvasInstance = bootstrap.Offcanvas.getInstance(sidebar);
      if (offcanvasInstance) {
        offcanvasInstance.hide();
      }
    }
  });
  window.addEventListener('popstate', updateSidebarActiveState);
})();

/**
 * ITAMbox Mobile Sidebar Swipe Gestures.
 *
 * Edge-swipe right (from the left screen edge) opens the offcanvas menu;
 * swipe left while it's open closes it. Only active below the lg breakpoint
 * where #sidebar-menu behaves as a Bootstrap offcanvas. Listeners are passive
 * so normal vertical scrolling is never blocked.
 */
(function () {
  const LG_BREAKPOINT = 992; // offcanvas is only active below this width
  const OPEN_EDGE = 30;      // px from the left edge that starts an opening swipe
  const THRESHOLD = 60;      // px of horizontal travel needed to trigger
  const VERTICAL_TOLERANCE = 45; // abandon if the gesture drifts mostly vertical

  let startX = 0;
  let startY = 0;
  let tracking = false;
  let fromEdge = false;

  function getSidebar(): HTMLElement | null {
    return document.getElementById('sidebar-menu');
  }
  function isMobile(): boolean {
    return window.innerWidth < LG_BREAKPOINT;
  }
  function isOpen(sb: HTMLElement): boolean {
    return sb.classList.contains('show');
  }
  function offcanvas(sb: HTMLElement) {
    if (typeof bootstrap === 'undefined' || !bootstrap.Offcanvas) return null;
    return bootstrap.Offcanvas.getOrCreateInstance(sb);
  }

  document.addEventListener(
    'touchstart',
    function (e: TouchEvent) {
      if (!isMobile() || e.touches.length !== 1) {
        tracking = false;
        return;
      }
      const sb = getSidebar();
      if (!sb) return;
      const t = e.touches[0];
      startX = t.clientX;
      startY = t.clientY;
      fromEdge = startX <= OPEN_EDGE;
      // Track an opening swipe (from the edge, while closed) or a closing swipe (while open).
      tracking = (fromEdge && !isOpen(sb)) || isOpen(sb);
    },
    { passive: true },
  );

  document.addEventListener(
    'touchmove',
    function (e: TouchEvent) {
      if (!tracking || !isMobile()) return;
      const sb = getSidebar();
      if (!sb) return;
      const t = e.touches[0];
      const dx = t.clientX - startX;
      const dy = t.clientY - startY;

      // Predominantly vertical movement → abandon and let the page scroll.
      if (Math.abs(dy) > Math.abs(dx)) {
        if (Math.abs(dy) > VERTICAL_TOLERANCE) tracking = false;
        return;
      }

      // Horizontal opening swipe from the edge: suppress the browser's own
      // edge back-gesture so ours wins (requires a cancelable, non-passive move).
      if (fromEdge && !isOpen(sb) && dx > 8 && e.cancelable) {
        e.preventDefault();
      }

      if (!isOpen(sb) && fromEdge && dx > THRESHOLD) {
        offcanvas(sb)?.show();
        tracking = false;
      } else if (isOpen(sb) && dx < -THRESHOLD) {
        offcanvas(sb)?.hide();
        tracking = false;
      }
    },
    { passive: false },
  );

  document.addEventListener(
    'touchend',
    function () {
      tracking = false;
    },
    { passive: true },
  );
})();
