/**
 * Handles slug field auto-generation from name fields,
 * including adding refresh buttons to slug fields.
 */

function initSlugHandling(container: HTMLElement | Document = document): void {
  const forms = container.querySelectorAll<HTMLFormElement>('form');

  forms.forEach((form) => {
    const nameField = form.querySelector<HTMLInputElement>('[name="name"]');
    const slugField = form.querySelector<HTMLInputElement>('[name="slug"]');

    if (nameField && slugField) {
      if ((slugField as any)._slugInitialized) return;
      (slugField as any)._slugInitialized = true;

      addRefreshButton(slugField, nameField);

      nameField.addEventListener('input', function () {
        if (!slugField.value) {
          slugField.value = slugify(nameField.value);
          // Notify form-dirty.ts of programmatic value change
          slugField.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });
    }
  });
}

function addRefreshButton(slugField: HTMLInputElement, nameField: HTMLInputElement): void {
  const inputGroup = document.createElement('div');
  inputGroup.classList.add('input-group');

  const parent = slugField.parentNode;
  if (parent) {
    parent.insertBefore(inputGroup, slugField);
    inputGroup.appendChild(slugField);
  }

  const refreshButton = document.createElement('button');
  refreshButton.setAttribute('type', 'button');
  refreshButton.classList.add('btn', 'btn-outline-secondary');
  refreshButton.innerHTML = '<i class="ti ti-refresh"></i>';
  refreshButton.title = 'Generate from name';

  inputGroup.appendChild(refreshButton);

  refreshButton.addEventListener('click', function () {
    if (nameField.value) {
      slugField.value = slugify(nameField.value);
      slugField.focus();
      // Notify form-dirty.ts of programmatic value change
      slugField.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

function slugify(text: string): string {
  return text
    .toString()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^\w\-]+/g, '')
    .replace(/\-\-+/g, '-')
    .replace(/^-+/, '')
    .replace(/-+$/, '');
}

// Binders
document.addEventListener('DOMContentLoaded', () => initSlugHandling(document));
document.body.addEventListener('htmx:afterSettle', (evt: Event) => {
  const detail = (evt as CustomEvent).detail;
  const target = detail?.elt || document;
  initSlugHandling(target);
});

