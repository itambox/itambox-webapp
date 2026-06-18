/**
 * Handles slug field auto-generation from name fields,
 * including adding refresh buttons to slug fields.
 *
 * The slug tracks the name field in real time until the user edits the slug
 * by hand (or the form loads with an existing slug, e.g. on edit forms), at
 * which point auto-tracking stops. The refresh button re-generates from the
 * current name and resumes tracking.
 */

interface SlugInput extends HTMLInputElement {
  _slugInitialized?: boolean;
  _slugManual?: boolean;
  _slugProgrammatic?: boolean;
}

/** Set the slug programmatically and notify listeners (e.g. form-dirty.ts). */
function setSlug(slugField: SlugInput, value: string): void {
  slugField.value = value;
  slugField._slugProgrammatic = true;
  slugField.dispatchEvent(new Event('input', { bubbles: true }));
  slugField._slugProgrammatic = false;
}

function initSlugHandling(container: HTMLElement | Document = document): void {
  const forms = container.querySelectorAll<HTMLFormElement>('form');

  forms.forEach((form) => {
    const nameField = form.querySelector<HTMLInputElement>('[name="name"]');
    const slugField = form.querySelector<SlugInput>('[name="slug"]');

    if (nameField && slugField) {
      if (slugField._slugInitialized) return;
      slugField._slugInitialized = true;

      // A slug that already has a value (edit forms) is treated as manual so
      // we never clobber it without an explicit refresh.
      slugField._slugManual = Boolean(slugField.value);

      addRefreshButton(slugField, nameField);

      // Any user-driven edit of the slug stops auto-tracking. Programmatic
      // updates (from setSlug) are ignored via the guard flag.
      slugField.addEventListener('input', function () {
        if (slugField._slugProgrammatic) return;
        slugField._slugManual = true;
      });

      nameField.addEventListener('input', function () {
        if (!slugField._slugManual) {
          setSlug(slugField, slugify(nameField.value));
        }
      });
    }
  });
}

function addRefreshButton(slugField: SlugInput, nameField: HTMLInputElement): void {
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
  refreshButton.innerHTML = '<i class="mdi mdi-refresh"></i>';
  refreshButton.title = gettext('Generate from name');

  inputGroup.appendChild(refreshButton);

  refreshButton.addEventListener('click', function () {
    if (nameField.value) {
      setSlug(slugField, slugify(nameField.value));
      slugField.focus();
      // Resume auto-tracking after an explicit regenerate.
      slugField._slugManual = false;
    }
  });
}

function slugify(text: string): string {
  return text
    .toString()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^\w-]+/g, '')
    .replace(/--+/g, '-')
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
