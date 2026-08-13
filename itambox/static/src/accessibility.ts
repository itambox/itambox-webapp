let pendingFocus: { id?: string; name?: string } | null = null;

function focusElement(element: HTMLElement): void {
  if (element.matches('h1, h2, h3, [data-focus-after-swap]') && !element.hasAttribute('tabindex')) {
    element.setAttribute('tabindex', '-1');
  }
  element.focus({ preventScroll: true });
}

function isInsideModal(element: Element | null): boolean {
  return Boolean(element?.closest('.modal'));
}

function focusableTrigger(trigger: HTMLElement): HTMLElement | null {
  if (trigger.matches('a, button, input, select, textarea, [tabindex]')) return trigger;
  return trigger.querySelector<HTMLElement>('input:focus, select:focus, textarea:focus, button:focus, a:focus')
    ?? trigger.querySelector<HTMLElement>('input, select, textarea, button, a, [tabindex]');
}

function rememberFocusedControl(event: Event): void {
  const detail = (event as CustomEvent).detail as { elt?: Element } | undefined;
  const element = detail?.elt;
  if (!(element instanceof HTMLElement)) return;
  const focused = element.matches(':focus') ? element : element.querySelector<HTMLElement>(':focus');
  if (!(focused instanceof HTMLInputElement || focused instanceof HTMLSelectElement || focused instanceof HTMLTextAreaElement)) {
    return;
  }
  pendingFocus = { id: focused.id || undefined, name: focused.name || undefined };
}

function focusAfterHtmxSettle(event: Event): void {
  const detail = (event as CustomEvent).detail as { target?: Element; elt?: Element } | undefined;
  const target = detail?.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.id === 'modal-placeholder' || isInsideModal(target)) {
    pendingFocus = null;
    return;
  }

  const invalid = target.querySelector<HTMLElement>('[aria-invalid="true"], .is-invalid');
  if (invalid) {
    focusElement(invalid);
    return;
  }

  if (pendingFocus) {
    const selector = pendingFocus.id
      ? `#${CSS.escape(pendingFocus.id)}`
      : pendingFocus.name
        ? `[name="${CSS.escape(pendingFocus.name)}"]`
        : '';
    const replacement = selector ? target.querySelector<HTMLElement>(selector) : null;
    pendingFocus = null;
    if (replacement) {
      focusElement(replacement);
      return;
    }
  }

  const trigger = detail?.elt;
  if (trigger instanceof HTMLElement && trigger.isConnected && !isInsideModal(trigger)) {
    const control = focusableTrigger(trigger);
    if (control) {
      focusElement(control);
      return;
    }
  }

  const heading = target.querySelector<HTMLElement>('[data-focus-after-swap], h1, h2, h3');
  if (heading) focusElement(heading);
}

document.body?.addEventListener('htmx:beforeRequest', rememberFocusedControl);
document.body?.addEventListener('htmx:afterSettle', focusAfterHtmxSettle);
