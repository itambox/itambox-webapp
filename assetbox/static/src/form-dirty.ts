/**
 * AssetBox — Dirty Form Tracking.
 *
 * Tracks modifications on forms and warns before HTMX navigation
 * or page unload when unsaved changes exist.
 *
 * Sets data-dirty="true" on modified forms and intercepts
 * htmx:beforeSwap / beforeunload to show a confirmation dialog.
 */
(function () {
  const dirtyForms = new WeakSet<HTMLFormElement>();

  function markDirty(form: HTMLFormElement): void {
    if (!form || dirtyForms.has(form)) return;
    dirtyForms.add(form);
    form.setAttribute('data-dirty', 'true');
  }

  function initForm(form: HTMLFormElement): void {
    if (!form || form.tagName !== 'FORM') return;
    if (form.hasAttribute('data-no-dirty-track')) return;

    form.querySelectorAll<HTMLElement>('input, select, textarea').forEach(function (el) {
      el.addEventListener('change', function () {
        markDirty(form);
      });
      el.addEventListener('input', function () {
        markDirty(form);
      });
    });

    form.addEventListener('submit', function () {
      dirtyForms.delete(form);
      form.removeAttribute('data-dirty');
    });

    form.addEventListener('reset', function () {
      dirtyForms.delete(form);
      form.removeAttribute('data-dirty');
    });
  }

  function hasAnyDirtyForm(): boolean {
    return document.querySelectorAll('form[data-dirty="true"]').length > 0;
  }

  // Intercept HTMX navigation when forms are dirty
  document.body.addEventListener('htmx:beforeSwap', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    if (!detail.boosted) return;
    if (!hasAnyDirtyForm()) return;

    if (!confirm('You have unsaved changes. Leave this page?')) {
      evt.preventDefault();
    }
  });

  // Intercept browser navigation/close
  window.addEventListener('beforeunload', function (evt) {
    if (hasAnyDirtyForm()) {
      evt.preventDefault();
    }
  });

  // Initialize existing forms on page load
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll<HTMLFormElement>('form').forEach(initForm);
  });

  // Initialize new forms after HTMX content swaps
  document.body.addEventListener('htmx:afterSettle', function () {
    document.querySelectorAll<HTMLFormElement>('form:not([data-dirty])').forEach(function (f) {
      if (!f.hasAttribute('data-no-dirty-track')) {
        f.querySelectorAll<HTMLElement>('input, select, textarea').forEach(function (el) {
          if (!(el as unknown as Record<string, unknown>)._dirtyTracked) {
            (el as unknown as Record<string, unknown>)._dirtyTracked = true;
            el.addEventListener('change', function () {
              markDirty(f);
            });
            el.addEventListener('input', function () {
              markDirty(f);
            });
          }
        });
      }
    });
  });
})();
