/**
 * Section-oriented specification editor behavior.
 *
 * The server remains the authority for definition resolution and patches. This
 * module only maintains the rendered draft controls and asks HTMX to request a
 * fresh form when the proposed definition changes.
 */

export type CompositionRefreshState = {
  draftDirty: boolean;
  sectionsCustomized: boolean;
};

/** Move one item by one adjacent position without mutating the input array. */
export function moveOrderedItem(items: readonly string[], index: number, delta: -1 | 1): string[] {
  const next = [...items];
  const target = index + delta;
  if (index < 0 || index >= next.length || target < 0 || target >= next.length) {
    return next;
  }
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** Category refreshes replace the draft definition and therefore need consent. */
export function compositionRefreshNeedsConfirmation(state: CompositionRefreshState): boolean {
  return state.draftDirty || state.sectionsCustomized;
}

function formElements(form: HTMLFormElement): HTMLElement[] {
  return Array.from(form.elements).filter((element): element is HTMLElement => element instanceof HTMLElement);
}

function namedElement(form: HTMLFormElement, name: string): HTMLInputElement | HTMLSelectElement | null {
  const element = formElements(form).find(
    (candidate) => candidate instanceof HTMLInputElement || candidate instanceof HTMLSelectElement
      ? candidate.name === name
      : false,
  );
  return (element as HTMLInputElement | HTMLSelectElement | undefined) ?? null;
}

function hasDraftInput(form: HTMLFormElement): boolean {
  if (form.dataset.dirty === 'true') return true;
  return formElements(form).some((element) => {
    if (!(element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement)) {
      return false;
    }
    if (element.type === 'hidden' || element.type === 'submit' || element.type === 'button') return false;
    if ((element as HTMLSelectElement).multiple) {
      const select = element as HTMLSelectElement;
      const initial = (select as unknown as { _initialValue?: string[] })._initialValue;
      if (initial) {
        return initial.length !== Array.from(select.selectedOptions).length
          || initial.some((value) => !Array.from(select.selectedOptions).some((option) => option.value === value));
      }
    }
    if (element instanceof HTMLInputElement && (element.type === 'checkbox' || element.type === 'radio')) {
      const initial = (element as unknown as { _initialChecked?: boolean })._initialChecked;
      return initial !== undefined ? element.checked !== initial : element.checked !== element.defaultChecked;
    }
    const defaultValue = element instanceof HTMLSelectElement
      ? (Array.from(element.options).find((option) => option.defaultSelected)?.value ?? element.options[0]?.value ?? '')
      : element.defaultValue;
    const initial = (element as unknown as { _initialValue?: string })._initialValue ?? defaultValue;
    return element.value !== initial;
  });
}

function textForConfirmation(): string {
  const translator = (globalThis as typeof globalThis & { gettext?: (value: string) => string }).gettext;
  return translator?.('Changing the specification sections will replace the draft definition. Continue?')
    ?? 'Changing the specification sections will replace the draft definition. Continue?';
}

function announce(form: HTMLFormElement, message: string): void {
  const live = form.querySelector<HTMLElement>('[data-specification-live]');
  if (live) live.textContent = message;
}

function hiddenSelectionInputs(form: HTMLFormElement): HTMLInputElement[] {
  return formElements(form).filter(
    (element): element is HTMLInputElement => element instanceof HTMLInputElement && element.name === 'custom_fieldsets',
  );
}

function selectedCards(form: HTMLFormElement): HTMLElement[] {
  const container = form.querySelector<HTMLElement>('[data-specification-selected-list]');
  return container
    ? Array.from(container.querySelectorAll<HTMLElement>('[data-specification-fieldset]'))
    : [];
}

function syncSectionPositions(form: HTMLFormElement, markCustomized = false): void {
  const cards = selectedCards(form);
  const ids = cards.map((card) => card.dataset.fieldsetId).filter((id): id is string => Boolean(id));
  hiddenSelectionInputs(form).forEach((input) => input.remove());
  ids.forEach((id) => {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'custom_fieldsets';
    input.value = id;
    form.appendChild(input);
  });
  const presence = namedElement(form, 'specification_fieldsets_presence');
  if (presence) presence.value = 'explicit';
  if (markCustomized) form.dataset.sectionsCustomized = 'true';
  cards.forEach((card, index) => {
    card.setAttribute('aria-posinset', String(index + 1));
    card.setAttribute('aria-setsize', String(cards.length));
    card.querySelectorAll<HTMLButtonElement>('[data-specification-move]').forEach((button) => {
      const direction = button.dataset.specificationMove;
      button.disabled = (direction === 'up' && index === 0) || (direction === 'down' && index === cards.length - 1);
    });
  });
}

function requestDefinitionRefresh(form: HTMLFormElement): void {
  const trigger = form.querySelector<HTMLButtonElement>('[data-specification-reload]');
  if (!trigger) return;
  trigger.hidden = false;
  trigger.click();
  trigger.hidden = true;
}

function moveCard(form: HTMLFormElement, card: HTMLElement, delta: -1 | 1): void {
  const cards = selectedCards(form);
  const index = cards.indexOf(card);
  const ids = cards.map((item) => item.dataset.fieldsetId ?? '');
  const next = moveOrderedItem(ids, index, delta);
  if (next.join('\u0000') === ids.join('\u0000')) return;
  const container = form.querySelector<HTMLElement>('[data-specification-selected-list]');
  if (!container) return;
  const destination = cards[index + delta];
  if (delta < 0) container.insertBefore(card, destination);
  else container.insertBefore(card, destination.nextSibling);
  syncSectionPositions(form, true);
  announce(form, `${card.dataset.fieldsetLabel ?? 'Specification section'} moved ${delta < 0 ? 'up' : 'down'}.`);
  requestDefinitionRefresh(form);
}

function moveCardBetweenLists(form: HTMLFormElement, card: HTMLElement, selected: boolean): void {
  const destination = form.querySelector<HTMLElement>(
    selected ? '[data-specification-selected-list]' : '[data-specification-available-list]',
  );
  if (!destination) return;
  destination.appendChild(card);
  card.dataset.selected = selected ? 'true' : 'false';
  card.querySelectorAll<HTMLInputElement>('[data-specification-fieldset-toggle]').forEach((input) => {
    input.checked = selected;
  });
  const retainedNotice = card.querySelector<HTMLElement>('[data-specification-retained-notice]');
  if (retainedNotice) retainedNotice.hidden = selected || !card.dataset.hasStoredValues;
  syncSectionPositions(form, true);
  announce(
    form,
    `${card.dataset.fieldsetLabel ?? 'Specification section'} ${selected ? 'added to' : 'removed from'} the draft.`,
  );
  requestDefinitionRefresh(form);
}

function restoreSelectValue(select: HTMLSelectElement, value: string): void {
  const tomSelect = (select as HTMLSelectElement & { tomselect?: { setValue: (value: string, silent?: boolean) => void } }).tomselect;
  if (tomSelect) {
    tomSelect.setValue(value, true);
  } else {
    select.value = value;
  }
}

function bindEditor(form: HTMLFormElement): void {
  if (form.dataset.specificationEditorBound === 'true') return;
  form.dataset.specificationEditorBound = 'true';

  const presence = namedElement(form, 'specification_fieldsets_presence');
  if (presence?.value === 'explicit') form.dataset.sectionsCustomized = 'true';
  const category = form.querySelector<HTMLSelectElement>('[data-specification-category]');
  if (category) category.dataset.previousSpecificationValue = category.value;

  form.addEventListener('focusin', (event) => {
    const target = event.target;
    if (target instanceof HTMLSelectElement && target.matches('[data-specification-category]')) {
      target.dataset.previousSpecificationValue = target.value;
    }
  });

  form.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;

    if (target.matches('[data-specification-category]') && target instanceof HTMLSelectElement) {
      const previous = target.dataset.previousSpecificationValue ?? target.value;
      if (compositionRefreshNeedsConfirmation({
        draftDirty: hasDraftInput(form),
        sectionsCustomized: form.dataset.sectionsCustomized === 'true',
      }) && !globalThis.confirm(textForConfirmation())) {
        restoreSelectValue(target, previous);
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      target.dataset.previousSpecificationValue = target.value;
      return;
    }

    if (target.matches('[data-specification-fieldset-toggle]') && target instanceof HTMLInputElement) {
      const card = target.closest<HTMLElement>('[data-specification-fieldset]');
      if (card) moveCardBetweenLists(form, card, target.checked);
      return;
    }

    if (target.matches('[data-specification-move]') && target instanceof HTMLButtonElement) {
      const card = target.closest<HTMLElement>('[data-specification-fieldset]');
      const delta = target.dataset.specificationMove === 'up' ? -1 : 1;
      if (card && (delta === -1 || delta === 1)) moveCard(form, card, delta);
      return;
    }

    if (target.matches('[data-specification-input]')) {
      const input = target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
      const key = input.dataset.specificationKey;
      if (!key) return;
      const marker = namedElement(form, `cf_${key}__presence`);
      if (marker) marker.value = 'value';
      const clear = namedElement(form, `cf_${key}__clear`);
      if (clear instanceof HTMLInputElement) clear.checked = false;
    }
  });

  form.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const move = target.closest<HTMLElement>('[data-specification-move]');
    if (move) {
      event.preventDefault();
      const card = move.closest<HTMLElement>('[data-specification-fieldset]');
      const direction = move.dataset.specificationMove;
      if (card && (direction === 'up' || direction === 'down')) {
        moveCard(form, card, direction === 'up' ? -1 : 1);
      }
      return;
    }
    const copy = target.closest<HTMLElement>('[data-specification-copy-model-key]');
    if (!copy) return;
    event.preventDefault();
    const key = copy.dataset.specificationCopyModelKey;
    const scriptId = copy.dataset.specificationModelValueScript;
    if (!key || !scriptId) return;
    const script = document.getElementById(scriptId);
    if (!script) return;
    let value: unknown;
    try {
      value = JSON.parse(script.textContent ?? 'null');
    } catch {
      return;
    }
    const inputs = formElements(form).filter(
      (element): element is HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement =>
        (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement)
        && element.dataset.specificationKey === key,
    );
    if (!inputs.length) return;
    const first = inputs[0];
    if (first instanceof HTMLSelectElement && first.multiple) {
      const selected = new Set(Array.isArray(value) ? value.map(String) : []);
      Array.from(first.options).forEach((option) => { option.selected = selected.has(option.value); });
    } else if (first instanceof HTMLInputElement && (first.type === 'checkbox' || first.type === 'radio')) {
      first.checked = value === true;
    } else {
      first.value = value === null || value === undefined ? '' : String(value);
    }
    const marker = namedElement(form, `cf_${key}__presence`);
    if (marker) marker.value = 'value';
    const clear = namedElement(form, `cf_${key}__clear`);
    if (clear instanceof HTMLInputElement) clear.checked = false;
    inputs.forEach((input) => input.dispatchEvent(new Event('input', { bubbles: true })));
    first.dispatchEvent(new Event('change', { bubbles: true }));
    const feedback = form.querySelector<HTMLElement>('[data-specification-copy-feedback]');
    if (feedback) {
      feedback.hidden = false;
      feedback.textContent = 'The model value was copied into the draft. Review it before saving.';
    }
  });

  syncSectionPositions(form);
}

function bindAllEditors(): void {
  document.querySelectorAll<HTMLFormElement>('[data-specification-editor]').forEach(bindEditor);
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', bindAllEditors);
  document.body?.addEventListener('htmx:afterSettle', bindAllEditors);
}
