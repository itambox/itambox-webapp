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

  function hasAnyDirtyForm(): boolean {
    return document.querySelectorAll('form[data-dirty="true"]').length > 0;
  }

  // Intercept HTMX navigation when forms are dirty
  document.body.addEventListener('htmx:beforeSwap', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    
    // Only block standard page transitions (GET requests)
    const method = detail.requestConfig?.method?.toLowerCase() || 'get';
    if (method !== 'get') return;
    
    if (!detail.boosted) return;
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

