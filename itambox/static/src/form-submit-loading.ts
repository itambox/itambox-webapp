/**
 * ITAMbox — Premium Form Submit Button Loading Indicators.
 *
 * Automatically injects loading spinners and disables submit buttons
 * during form submissions (both HTMX requests and standard page reloads)
 * to provide crisp visual feedback and prevent accidental double-submits.
 */
(function () {
  let activeSubmits: Map<HTMLFormElement, HTMLElement> = new Map();

  function showLoadingState(form: HTMLFormElement, submitter?: HTMLElement): void {
    if (!form || activeSubmits.has(form)) return;

    let submitBtn = submitter;
    if (!submitBtn || submitBtn === form) {
      submitBtn = form.querySelector<HTMLButtonElement | HTMLInputElement>(
        'button[type="submit"], input[type="submit"], .btn-primary'
      ) as HTMLElement | null;
    }

    if (!submitBtn) return;

    // Store original contents to restore on completion/error
    const originalText = submitBtn instanceof HTMLInputElement ? submitBtn.value : submitBtn.innerHTML;
    (submitBtn as any)._originalContent = originalText;
    (submitBtn as any)._originalDisabledState = (submitBtn as any).disabled;

    // If the button has a name, create a hidden input to preserve its value during form submission
    if (submitBtn instanceof HTMLButtonElement || submitBtn instanceof HTMLInputElement) {
      const name = submitBtn.name;
      if (name) {
        const val = submitBtn.getAttribute('value') || submitBtn.value || '1';
        const hiddenInput = document.createElement('input');
        hiddenInput.type = 'hidden';
        hiddenInput.name = name;
        hiddenInput.value = val;
        hiddenInput.setAttribute('data-added-by-loading', 'true');
        form.appendChild(hiddenInput);
      }
    }

    // Disable button to prevent double clicks
    if ('disabled' in submitBtn) {
      (submitBtn as any).disabled = true;
    }
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

    // Clean up dynamic hidden input if present
    if (submitBtn instanceof HTMLButtonElement || submitBtn instanceof HTMLInputElement) {
      const name = submitBtn.name;
      if (name) {
        const hiddenInput = form.querySelector(`input[type="hidden"][name="${name}"][data-added-by-loading="true"]`);
        if (hiddenInput) {
          hiddenInput.remove();
        }
      }
    }

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
    if ('disabled' in submitBtn) {
      (submitBtn as any).disabled = originalDisabled !== undefined ? originalDisabled : false;
    }
    submitBtn.classList.remove('disabled');

    activeSubmits.delete(form);
  }

  // --- 1. Traditional Form Submission Event ---
  document.body.addEventListener('submit', function (evt) {
    const form = evt.target as HTMLFormElement;
    if (form && form.tagName === 'FORM') {
      const submitter = (evt as SubmitEvent).submitter || undefined;
      showLoadingState(form, submitter);
    }
  });

  // --- 2. HTMX Integration ---
  // Intercept HTMX request start
  document.body.addEventListener('htmx:configRequest', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const form = detail.form || (detail.elt ? detail.elt.closest('form') : null);
    if (form) {
      showLoadingState(form, detail.elt);
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
