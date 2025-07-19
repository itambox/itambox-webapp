(function() {
    function updateSidebarActiveState() {
        var currentPath = window.location.pathname;
        var sidebar = document.getElementById('sidebar-menu');
        if (!sidebar) return;

        sidebar.querySelectorAll('.nav-item.active, .nav-link.active, .dropdown-item.active, .dropdown-menu.show')
            .forEach(function(el) {
                el.classList.remove('active');
                if (el.classList.contains('dropdown-menu')) {
                    el.classList.remove('show');
                }
            });

        var bestMatch = null;
        var bestMatchLength = 0;

        sidebar.querySelectorAll('.dropdown-item a[href], .nav-item > .nav-link[href]').forEach(function(link) {
            var href = link.getAttribute('href');
            if (!href || href === '#') return;

            var resolved;
            try {
                resolved = new URL(href, window.location.origin).pathname;
            } catch (e) {
                return;
            }

            if (currentPath === resolved || (resolved !== '/' && currentPath.indexOf(resolved + '/') === 0)) {
                if (resolved.length > bestMatchLength) {
                    bestMatch = link;
                    bestMatchLength = resolved.length;
                }
            }
        });

        if (bestMatch) {
            var dropdownItem = bestMatch.closest('.dropdown-item');
            if (dropdownItem) dropdownItem.classList.add('active');

            var navItem = bestMatch.closest('.nav-item');
            if (navItem) {
                navItem.classList.add('active');
                var navLink = navItem.querySelector(':scope > .nav-link');
                if (navLink) navLink.classList.add('active');
                var dropdownMenu = navItem.querySelector(':scope > .dropdown-menu');
                if (dropdownMenu) dropdownMenu.classList.add('show');
            }
        } else if (currentPath === '/' || currentPath === '') {
            var homeItem = sidebar.querySelector('.nav-item:first-child');
            if (homeItem) homeItem.classList.add('active');
        }
    }

    document.addEventListener('DOMContentLoaded', updateSidebarActiveState);
    document.body.addEventListener('htmx:afterSettle', function() {
        updateSidebarActiveState();
    });
    window.addEventListener('popstate', updateSidebarActiveState);
})();
