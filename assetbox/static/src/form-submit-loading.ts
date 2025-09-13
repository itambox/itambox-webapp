/**
 * AssetBox — Premium Form Submit Button Loading Indicators.
 *
 * Automatically injects loading spinners and disables submit buttons
 * during form submissions (both HTMX requests and standard page reloads)
 * to provide crisp visual feedback and prevent accidental double-submits.
 */
(function () {
  let activeSubmits: Map<HTMLFormElement, HTMLElement> = new Map();

  function showLoadingState(form: HTMLFormElement): void {
    if (!form || activeSubmits.has(form)) return;

    // Find the primary submit button in the form
    const submitBtn = form.querySelector<HTMLButtonElement | HTMLInputElement>(
      'button[type="submit"], input[type="submit"], .btn-primary'
    );

    if (!submitBtn) return;

    // Store original contents to restore on completion/error
    const originalText = submitBtn instanceof HTMLInputElement ? submitBtn.value : submitBtn.innerHTML;
    (submitBtn as any)._originalContent = originalText;
    (submitBtn as any)._originalDisabledState = submitBtn.disabled;

    // Disable button to prevent double clicks
    submitBtn.disabled = true;
    submitBtn.classList.add('disabled');

    // Inject spinner based on element type
    if (submitBtn instanceof HTMLInputElement) {
      submitBtn.value = 'Saving...';
    } else {
      const spinnerHtml = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>';
      submitBtn.innerHTML = spinnerHtml + submitBtn.textContent?.trim();
    }

    activeSubmits.set(form, submitBtn);
  }

  function restoreLoadingState(form: HTMLFormElement): void {
    const submitBtn = activeSubmits.get(form);
    if (!submitBtn) return;

    // Restore original content and disabled status
    const originalContent = (submitBtn as any)._originalContent;
    if (originalContent !== undefined) {
      if (submitBtn instanceof HTMLInputElement) {
        submitBtn.value = originalContent;
      } else {
        submitBtn.innerHTML = originalContent;
      }
    }

    const originalDisabled = (submitBtn as any)._originalDisabledState;
    submitBtn.disabled = originalDisabled !== undefined ? originalDisabled : false;
    submitBtn.classList.remove('disabled');

    activeSubmits.delete(form);
  }

  // --- 1. Traditional Form Submission Event ---
  document.body.addEventListener('submit', function (evt) {
    const form = evt.target as HTMLFormElement;
    if (form && form.tagName === 'FORM') {
      showLoadingState(form);
    }
  });

  // --- 2. HTMX Integration ---
  // Intercept HTMX request start
  document.body.addEventListener('htmx:configRequest', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const form = detail.form || (detail.elt ? detail.elt.closest('form') : null);
    if (form) {
      showLoadingState(form);
    }
  });

  // Intercept HTMX response / swap finished to restore button state
  document.body.addEventListener('htmx:afterRequest', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const form = detail.elt ? detail.elt.closest('form') : null;
    if (form) {
      restoreLoadingState(form);
    }
  });

  // In case of error or timeout, ensure we restore button state
  document.body.addEventListener('htmx:sendError', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const form = detail.elt ? detail.elt.closest('form') : null;
    if (form) {
      restoreLoadingState(form);
    }
  });
})();
