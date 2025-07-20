/**
 * AssetBox Sidebar — Active state tracking with URL matching.
 *
 * Updates .nav-item.active, .nav-link.active, .dropdown-item.active
 * based on the current page URL. Handles HTMX navigation (afterSettle)
 * and browser popstate.
 */
(function () {
  function updateSidebarActiveState(): void {
    const currentPath = window.location.pathname;
    const sidebar = document.getElementById('sidebar-menu');
    if (!sidebar) return;

    sidebar.querySelectorAll('.nav-item.active, .nav-link.active, .dropdown-item.active, .dropdown-menu.show').forEach(
      function (el: Element) {
        el.classList.remove('active');
        if (el.classList.contains('dropdown-menu')) {
          el.classList.remove('show');
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

        if (currentPath === resolved || (resolved !== '/' && currentPath.indexOf(resolved + '/') === 0)) {
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
        if (navLink) navLink.classList.add('active');
        const dropdownMenu = navItem.querySelector<HTMLElement>(':scope > .dropdown-menu');
        if (dropdownMenu) dropdownMenu.classList.add('show');
      }
    } else if (currentPath === '/' || currentPath === '') {
      const homeItem = sidebar.querySelector('.nav-item:first-child');
      if (homeItem) homeItem.classList.add('active');
    }
  }

  document.addEventListener('DOMContentLoaded', updateSidebarActiveState);
  document.body.addEventListener('htmx:afterSettle', function () {
    updateSidebarActiveState();
  });
  window.addEventListener('popstate', updateSidebarActiveState);
})();
