/**
 * AssetBox Filter Toggle — manages filter sidebar visibility.
 *
 * Exposes window.toggleFilters() and window.initFiltersToggle() for use
 * in object list pages and HTMX lifecycle hooks.
 */
(function() {
    function toggleFilters() {
        var mainCol = document.getElementById('object-list-main-col');
        var filterCol = document.getElementById('object-list-filter-col');
        var toggleBtn = document.getElementById('toggle-filters-btn');

        if (!filterCol || !mainCol) return;

        var isHidden = filterCol.classList.contains('d-none');
        if (isHidden) {
            filterCol.classList.remove('d-none');
            mainCol.classList.remove('col-md-12');
            mainCol.classList.add('col-md-9');
            if (toggleBtn) {
                toggleBtn.classList.add('active');
                toggleBtn.classList.remove('btn-outline-secondary');
                toggleBtn.classList.add('btn-outline-primary');
            }
            localStorage.setItem('assetbox-show-filters', 'true');
        } else {
            filterCol.classList.add('d-none');
            mainCol.classList.remove('col-md-9');
            mainCol.classList.add('col-md-12');
            if (toggleBtn) {
                toggleBtn.classList.remove('active');
                toggleBtn.classList.remove('btn-outline-primary');
                toggleBtn.classList.add('btn-outline-secondary');
            }
            localStorage.setItem('assetbox-show-filters', 'false');
        }
    }

    function initFiltersToggle() {
        var mainCol = document.getElementById('object-list-main-col');
        var filterCol = document.getElementById('object-list-filter-col');
        var toggleBtn = document.getElementById('toggle-filters-btn');

        if (!filterCol || !mainCol) return;

        var storedPreference = localStorage.getItem('assetbox-show-filters');
        var isMobile = window.innerWidth < 768;

        var showFilters = true;
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

    window.toggleFilters = toggleFilters;
    window.initFiltersToggle = initFiltersToggle;

    // Delegate click on #toggle-filters-btn at document level
    document.addEventListener('click', function(event) {
        var toggleBtn = event.target.closest('#toggle-filters-btn');
        if (toggleBtn) {
            event.preventDefault();
            toggleFilters();
        }
    });

    // Re-initialize when body content transitions or dynamic content loads
    document.body.addEventListener('htmx:afterOnLoad', function(event) {
        if (event.detail.target && (event.detail.target.id === 'page-body-main' || event.detail.target.id === 'object-list-dynamic-content' || event.detail.target.id === 'page-content-wrapper')) {
            initFiltersToggle();
        }
    });

    // Initial call on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initFiltersToggle);
    } else {
        initFiltersToggle();
    }
})();
