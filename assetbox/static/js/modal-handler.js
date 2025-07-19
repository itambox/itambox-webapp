/**
 * AssetBox — HTMX Modal Auto-Show Handler.
 *
 * When HTMX loads modal HTML into #modal-placeholder, this module
 * automatically detects the new .modal element and shows it via
 * Bootstrap's Modal API.
 *
 * This replaces the old pattern of inline hx-on::after-request JS
 * (which required 'unsafe-eval' in CSP) with a clean event listener.
 */
(function() {
    document.body.addEventListener('htmx:afterSettle', function(evt) {
        // Only handle swaps that targeted the modal placeholder
        if (!evt.detail || !evt.detail.target) return;
        
        var target = evt.detail.target;
        if (target.id !== 'modal-placeholder') return;

        // Find any .modal inside the placeholder and show it
        var modals = target.querySelectorAll('.modal');
        modals.forEach(function(modal) {
            // Skip if already visible or if Bootstrap already has an instance showing
            if (modal.classList.contains('show')) return;
            try {
                var inst = bootstrap.Modal.getOrCreateInstance(modal);
                if (!inst._isShown) {
                    inst.show();
                }
            } catch (e) {
                // Bootstrap not available or modal not properly formed
                console.warn('AssetBox modal auto-show failed:', e);
            }
        });
    });
})();
