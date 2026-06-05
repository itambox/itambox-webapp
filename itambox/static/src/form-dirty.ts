/**
 * ITAMbox — Dirty Form Tracking.
 *
 * Tracks modifications on forms and warns before HTMX navigation
 * or page unload when unsaved changes exist.
 *
 * Sets data-dirty="true" on modified forms and intercepts
 * htmx:beforeSwap / beforeunload to show a confirmation dialog.
 */
(function () {
  function isElementDirty(el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement): boolean {
    if (el instanceof HTMLSelectElement && el.multiple) {
      const initial = (el as any)._initialValue as string[] || [];
      const current = Array.from(el.options).filter(opt => opt.selected).map(opt => opt.value);
      if (initial.length !== current.length) return true;
      return !initial.every(val => current.includes(val));
    }
    
    if (el.type === 'checkbox' || el.type === 'radio') {
      const initial = '_initialChecked' in el ? (el as any)._initialChecked : el.defaultChecked;
      return el.checked !== initial;
    }
    
    const initial = '_initialValue' in el ? (el as any)._initialValue : el.defaultValue;
    return el.value !== initial;
  }

  function isFormVisible(form: HTMLFormElement): boolean {
    return !!(form.offsetWidth || form.offsetHeight || form.getClientRects().length);
  }

  function checkFormDirty(form: HTMLFormElement): void {
    if (form.hasAttribute('data-no-dirty-track')) return;

    let isDirty = false;
    const elements = form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]), select, textarea'
    );

    for (let i = 0; i < elements.length; i++) {
      if (isElementDirty(elements[i])) {
        isDirty = true;
        break;
      }
    }

    if (isDirty) {
      form.setAttribute('data-dirty', 'true');
    } else {
      form.removeAttribute('data-dirty');
    }
  }

  function clearFormDirty(form: HTMLFormElement): void {
    form.removeAttribute('data-dirty');
  }

  function initForm(form: HTMLFormElement): void {
    if (!form || form.tagName !== 'FORM') return;
    if (form.hasAttribute('data-no-dirty-track')) return;

    // Ignore GET forms (searches, filters, etc.) as they are read-only / idempotent
    const method = form.getAttribute('method')?.toLowerCase();
    const hasWriteHx = form.hasAttribute('hx-post') || form.hasAttribute('hx-put') || form.hasAttribute('hx-patch') || form.hasAttribute('hx-delete');

    // Only track if it's explicitly a data-modifying form (POST/PUT/PATCH/DELETE)
    if (method !== 'post' && method !== 'put' && method !== 'patch' && !hasWriteHx) {
      return;
    }

    form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]), select, textarea'
    ).forEach(function (el) {
      if ((el as any)._dirtyTracked) return;
      (el as any)._dirtyTracked = true;

      // Store initial value
      if (el instanceof HTMLSelectElement && el.multiple) {
        (el as any)._initialValue = Array.from(el.options).filter(opt => opt.selected).map(opt => opt.value);
      } else if (el.type === 'checkbox' || el.type === 'radio') {
        (el as any)._initialChecked = el.checked;
      } else {
        (el as any)._initialValue = el.value;
      }

      const handler = function () {
        checkFormDirty(form);
      };
      el.addEventListener('change', handler);
      el.addEventListener('input', handler);
    });

    if (!(form as any)._submitListened) {
      (form as any)._submitListened = true;
      form.addEventListener('submit', function () {
        clearFormDirty(form);
      });

      form.addEventListener('reset', function () {
        setTimeout(function () {
          checkFormDirty(form);
        }, 0);
      });
    }
  }

  let currentUrl = window.location.href;

  function updateCurrentUrl() {
    currentUrl = window.location.href;
  }

  document.body.addEventListener('htmx:afterNavigate', updateCurrentUrl);
  document.body.addEventListener('htmx:historyRestore', updateCurrentUrl);

  function restoreHistoryUrl() {
    history.pushState(null, '', currentUrl);
  }

  function hasAnyDirtyForm(): boolean {
    const dirtyForms = document.querySelectorAll('form[data-dirty="true"]');
    for (let i = 0; i < dirtyForms.length; i++) {
      const form = dirtyForms[i] as HTMLFormElement;
      if (!isFormVisible(form)) continue;
      if (form.hasAttribute('data-no-dirty-track')) continue;

      const method = form.getAttribute('method')?.toLowerCase();
      const hasWriteHx = form.hasAttribute('hx-post') || form.hasAttribute('hx-put') || form.hasAttribute('hx-patch') || form.hasAttribute('hx-delete');

      // Double check it's explicitly a data-modifying form (POST/PUT/PATCH/DELETE)
      if (method !== 'post' && method !== 'put' && method !== 'patch' && !hasWriteHx) {
        continue;
      }

      return true;
    }
    return false;
  }

  // Intercept HTMX history navigation (Back/Forward browser buttons) when forms are dirty
  document.body.addEventListener('htmx:historyCacheHit', function (evt) {
    if (hasAnyDirtyForm()) {
      if (!confirm('You have unsaved changes. Leave this page?')) {
        evt.preventDefault();
        restoreHistoryUrl();
      }
    }
  });

  document.body.addEventListener('htmx:historyCacheMiss', function (evt) {
    if (hasAnyDirtyForm()) {
      if (!confirm('You have unsaved changes. Leave this page?')) {
        evt.preventDefault();
        restoreHistoryUrl();
      }
    }
  });

  // Intercept HTMX navigation when forms are dirty
  document.body.addEventListener('htmx:beforeSwap', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    
    // Only block standard page transitions (GET requests)
    const method = detail.requestConfig?.method?.toLowerCase() || 'get';
    if (method !== 'get') return;
    
    if (!detail.boosted) return;

    // If the triggering element has data-no-dirty-track or is a cancel button, skip warning
    const triggerEl = detail.elt as HTMLElement | null;
    if (triggerEl) {
      if (triggerEl.hasAttribute('data-no-dirty-track') || triggerEl.closest('[data-no-dirty-track]')) {
        return;
      }
      // Check for generic Cancel buttons/links
      if (triggerEl.classList.contains('btn-outline-secondary') || triggerEl.classList.contains('btn-secondary')) {
        const text = triggerEl.textContent?.trim().toLowerCase() || '';
        if (text === 'cancel' || text === 'abbrechen') {
          return;
        }
      }
    }

    if (!hasAnyDirtyForm()) return;

    if (!confirm('You have unsaved changes. Leave this page?')) {
      evt.preventDefault();
    }
  });

  // Clear dirty state of a form when it is submitted via HTMX
  document.body.addEventListener('htmx:configRequest', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const form = detail.form || (detail.elt ? detail.elt.closest('form') : null);
    if (form) {
      clearFormDirty(form);
    }
  });

  // Intercept browser navigation/close
  window.addEventListener('beforeunload', function (evt) {
    if (hasAnyDirtyForm()) {
      evt.preventDefault();
      evt.returnValue = ''; // Required by modern browsers to trigger confirmation prompt
      return ''; // Fallback for older browsers
    }
  });

  // Initialize existing forms on page load (deferred to let other components initialize)
  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(function () {
      document.querySelectorAll<HTMLFormElement>('form').forEach(initForm);
    }, 0);
  });

  // Initialize new forms after HTMX content swaps (deferred to let other components initialize)
  document.body.addEventListener('htmx:afterSettle', function () {
    setTimeout(function () {
      document.querySelectorAll<HTMLFormElement>('form').forEach(initForm);
    }, 0);
  });
})();

