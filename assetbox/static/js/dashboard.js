/**
 * AssetBox Dashboard — GridStack integration with HTMX lifecycle management.
 *
 * Replaces inline script in dashboard.html. Handles:
 *  - GridStack initialization with Bootstrap grid fallback
 *  - Lock/unlock toggle
 *  - Save layout (with CSRF token fallback to cookie)
 *  - HTMX beforeSwap/afterSettle reinit
 */
(function() {
    var grid = null;
    var gsLoaded = false;

    function getCSRFToken() {
        return AssetBoxState.getCSRFToken();
    }

    function initGridStack() {
        try {
            if (window.__gsInitialized) return;
            var el = document.getElementById('dashboard-grid');
            if (!el) return;

            // Collect Bootstrap cols BEFORE removing classes
            var cols = [];
            Array.from(el.children).forEach(function(child) {
                if (child.className && /col-lg-\d+/.test(child.className)) cols.push(child);
            });

            if (cols.length === 0) return;

            // Remove Bootstrap grid classes from container
            el.classList.remove('row', 'row-cards');

            cols.forEach(function(col, i) {
                var card = col.querySelector('.card');
                if (!card) return;

                // Read saved positions from data attributes
                var savedW = col.getAttribute('data-gs-w');
                var savedH = col.getAttribute('data-gs-h');
                var savedX = col.getAttribute('data-gs-x');
                var savedY = col.getAttribute('data-gs-y');

                // Remove ALL Bootstrap column classes
                col.className = col.className.replace(/col\S+/g, '').trim();
                col.classList.add('grid-stack-item');

                // Apply saved sizes (or defaults)
                col.setAttribute('gs-w', savedW || '4');
                col.setAttribute('gs-h', savedH || '2');
                col.setAttribute('gs-id', 'widget-' + i);

                // Apply saved position (falsy values = not set, autoposition)
                if (savedX) col.setAttribute('gs-x', savedX);
                if (savedY) col.setAttribute('gs-y', savedY);

                card.classList.add('grid-stack-item-content');
            });

            grid = GridStack.init({
                column: 12,
                cellHeight: 100,
                margin: 8,
                disableDrag: true,
                disableResize: true,
                draggable: { handle: '.card-header' },
                resizable: { handles: 'e, se, s, sw, w' },
            }, el);

            // GridStack.init succeeded — mark as loaded
            gsLoaded = true;
            window.__gsInitialized = true;

        } catch(e) {
            console.warn('GridStack init error — using Bootstrap grid fallback:', e);
            // Restore Bootstrap classes so the fallback layout works
            var el = document.getElementById('dashboard-grid');
            if (el && !el.classList.contains('row')) {
                el.classList.add('row', 'row-cards');
                Array.from(el.children).forEach(function(child) {
                    var w = child.getAttribute('gs-w') || child.getAttribute('data-gs-w') || '4';
                    child.classList.add('col-lg-' + w, 'col-md-6', 'col-12');
                    child.classList.remove('grid-stack-item');
                    var card = child.querySelector('.grid-stack-item-content');
                    if (card) card.classList.remove('grid-stack-item-content');
                });
            }
            window.__gsInitialized = false;
        }
    }

    function destroyGridStack() {
        if (grid) {
            try { grid.destroy(false); } catch(e) {}
            grid = null;
        }
        gsLoaded = false;
        window.__gsInitialized = false;
    }

    function toggleLock() {
        if (!grid || !gsLoaded) return;
        var wasLocked = grid.opts.disableDrag;

        grid.enableMove(wasLocked);
        grid.enableResize(wasLocked);

        var isNowLocked = !wasLocked;

        var lockedEl = document.getElementById('dashboard-locked-controls');
        var unlockedEl = document.getElementById('dashboard-unlocked-controls');
        if (lockedEl) lockedEl.style.display = isNowLocked ? '' : 'none';
        if (unlockedEl) unlockedEl.style.display = isNowLocked ? 'none' : '';

        document.querySelectorAll('#dashboard-grid .card').forEach(function(card) {
            card.style.outline = isNowLocked ? '' : '2px dashed var(--tblr-primary)';
        });
        document.querySelectorAll('.dashboard-manage-btn').forEach(function(btn) {
            btn.classList.toggle('d-none', isNowLocked);
        });
    }

    function saveLayout() {
        if (!grid || !gsLoaded) return;
        var items = grid.save(false);
        var widgets = items.map(function(item) {
            var id = item.id || '';
            var index = parseInt(id.replace('widget-', ''));
            return { index: isNaN(index) ? 0 : index, x: item.x || 0, y: item.y || 0, w: item.w || 4, h: item.h || 2 };
        });

        fetch('/extras/dashboard/save-layout/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            },
            body: JSON.stringify({ widgets: widgets })
        }).then(function(r) {
            if (!r.ok) return;
            var btn = document.getElementById('save-dashboard');
            if (!btn || btn.dataset._saving === 'true') return;
            btn.dataset._saving = 'true';
            var origHTML = btn.innerHTML;
            btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-check me-1" width="20" height="20" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M5 12l5 5l10 -10"/></svg>Saved';
            btn.classList.add('btn-success');
            btn.classList.remove('btn-primary');
            setTimeout(function() {
                btn.innerHTML = origHTML;
                btn.classList.remove('btn-success');
                btn.classList.add('btn-primary');
                btn.dataset._saving = 'false';
            }, 1500);
        });
    }

    // --- Delegated click handler (survives DOM swaps) ---
    document.addEventListener('click', function(evt) {
        var btn = evt.target.closest('button');
        if (!btn) return;
        switch (btn.id) {
            case 'unlock-dashboard': toggleLock(); break;
            case 'lock-dashboard':   toggleLock(); saveLayout(); break;
            case 'save-dashboard':   saveLayout(); break;
        }
    });

    // --- Init when DOM ready ---
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initGridStack);
    } else {
        initGridStack();
    }

    // --- HTMX lifecycle: destroy GridStack before navigating away ---
    // Only clear state when the dashboard itself is being replaced (not modals)
    document.body.addEventListener('htmx:beforeSwap', function(evt) {
        var target = evt.detail.target;
        if (!target || !target.querySelector) return;
        if (target.querySelector('#dashboard-grid')) {
            grid = null;
            gsLoaded = false;
            window.__gsInitialized = false;
        }
    });

    // --- HTMX lifecycle: reinitialize after history restore or content swap ---
    document.body.addEventListener('htmx:afterSettle', function() {
        var gridEl = document.getElementById('dashboard-grid');
        if (!gridEl) return;
        if (!gridEl.classList.contains('grid-stack')) {
            window.__gsInitialized = false;
            gsLoaded = false;
            initGridStack();
        }
    });
})();
