/**
 * CSP-safe replacements for inline event handlers.
 *
 * data-confirm="msg"         — intercept form submit; show confirm() before proceeding
 * data-copy-input="#selector" — copy the referenced input's value to clipboard
 * data-copy-value="literal"  — copy a literal string to clipboard
 * data-action="print"        — call window.print()
 *
 * For copy buttons, data-copy-feedback sets the temporary success label (default "Copied!").
 */
(function () {
  function handleConfirm(evt: Event): void {
    const btn = evt.currentTarget as HTMLButtonElement;
    const msg = btn.getAttribute('data-confirm') || 'Are you sure?';
    if (!confirm(msg)) {
      evt.preventDefault();
    }
  }

  function handleCopy(evt: Event): void {
    const btn = evt.currentTarget as HTMLButtonElement;
    let text: string | null = null;

    const inputSel = btn.getAttribute('data-copy-input');
    if (inputSel) {
      const input = document.querySelector<HTMLInputElement>(inputSel);
      text = input ? input.value : null;
    } else {
      text = btn.getAttribute('data-copy-value');
    }

    if (!text) return;

    navigator.clipboard.writeText(text).then(function () {
      const feedback = btn.getAttribute('data-copy-feedback') || 'Copied!';
      const original = btn.textContent || '';
      btn.textContent = feedback;
      setTimeout(function () {
        btn.textContent = original;
      }, 2000);
    });
  }

  function handleFill(evt: Event): void {
    evt.preventDefault();
    const el = evt.currentTarget as HTMLElement;
    const targetId = el.getAttribute('data-fill-target');
    const value = el.getAttribute('data-fill-value') || '';
    if (!targetId) return;
    const input = document.getElementById(targetId) as HTMLInputElement | null;
    if (input) {
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function handlePrint(): void {
    window.print();
  }

  function bind(root: Document | HTMLElement): void {
    root.querySelectorAll<HTMLButtonElement>('[data-confirm]').forEach(function (btn) {
      if ((btn as any)._inlineActionsBound) return;
      (btn as any)._inlineActionsBound = true;
      btn.addEventListener('click', handleConfirm);
    });

    root.querySelectorAll<HTMLElement>('[data-fill-target]').forEach(function (el) {
      if ((el as any)._inlineActionsBound) return;
      (el as any)._inlineActionsBound = true;
      el.addEventListener('click', handleFill);
    });

    root.querySelectorAll<HTMLButtonElement>('[data-copy-input], [data-copy-value]').forEach(function (btn) {
      if ((btn as any)._inlineActionsBound) return;
      (btn as any)._inlineActionsBound = true;
      btn.addEventListener('click', handleCopy);
    });

    root.querySelectorAll<HTMLButtonElement>('[data-action="print"]').forEach(function (btn) {
      if ((btn as any)._inlineActionsBound) return;
      (btn as any)._inlineActionsBound = true;
      btn.addEventListener('click', handlePrint);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    bind(document);
  });

  document.body.addEventListener('htmx:afterSettle', function () {
    bind(document);
  });
})();
