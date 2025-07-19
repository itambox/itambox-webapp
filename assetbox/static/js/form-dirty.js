/**
 * AssetBox — Dirty Form Tracking.
 *
 * Tracks modifications on forms and warns before HTMX navigation
 * or page unload when unsaved changes exist.
 *
 * Sets data-dirty="true" on modified forms and intercepts
 * htmx:beforeSwap / beforeunload to show a confirmation dialog.
 */
(function() {
    var dirtyForms = new WeakSet();

    function markDirty(form) {
        if (!form || dirtyForms.has(form)) return;
        dirtyForms.add(form);
        form.setAttribute('data-dirty', 'true');
    }

    function isDirty(form) {
        return dirtyForms.has(form);
    }

    function initForm(form) {
        if (!form || form.tagName !== 'FORM') return;
        if (form.hasAttribute('data-no-dirty-track')) return;

        form.querySelectorAll('input, select, textarea').forEach(function(el) {
            el.addEventListener('change', function() { markDirty(form); });
            el.addEventListener('input', function() { markDirty(form); });
        });

        form.addEventListener('submit', function() {
            dirtyForms.delete(form);
            form.removeAttribute('data-dirty');
        });

        form.addEventListener('reset', function() {
            dirtyForms.delete(form);
            form.removeAttribute('data-dirty');
        });
    }

    function hasAnyDirtyForm() {
        var forms = document.querySelectorAll('form[data-dirty="true"]');
        return forms.length > 0;
    }

    // Intercept HTMX navigation when forms are dirty
    document.body.addEventListener('htmx:beforeSwap', function(evt) {
        // Only intercept boosted navigation (full page swaps), not inline form submissions
        if (!evt.detail.boosted) return;
        if (!hasAnyDirtyForm()) return;

        if (!confirm('You have unsaved changes. Leave this page?')) {
            evt.preventDefault();
        }
    });

    // Intercept browser navigation/close
    window.addEventListener('beforeunload', function(evt) {
        if (hasAnyDirtyForm()) {
            evt.preventDefault();
        }
    });

    // Initialize existing forms on page load
    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('form').forEach(initForm);
    });

    // Initialize new forms after HTMX content swaps
    document.body.addEventListener('htmx:afterSettle', function() {
        document.querySelectorAll('form:not([data-dirty])').forEach(function(f) {
            if (!f.hasAttribute('data-no-dirty-track')) {
                f.querySelectorAll('input, select, textarea').forEach(function(el) {
                    if (!el._dirtyTracked) {
                        el._dirtyTracked = true;
                        el.addEventListener('change', function() { markDirty(f); });
                        el.addEventListener('input', function() { markDirty(f); });
                    }
                });
            }
        });
    });
})();
