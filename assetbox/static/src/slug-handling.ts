/**
 * Handles slug field auto-generation from name fields,
 * including adding refresh buttons to slug fields.
 */

document.addEventListener('DOMContentLoaded', function () {
  const forms = document.querySelectorAll<HTMLFormElement>('form');

  forms.forEach((form) => {
    const nameField = form.querySelector<HTMLInputElement>('[name="name"]');
    const slugField = form.querySelector<HTMLInputElement>('[name="slug"]');

    if (nameField && slugField) {
      addRefreshButton(slugField, nameField);

      nameField.addEventListener('input', function () {
        if (!slugField.value) {
          slugField.value = slugify(nameField.value);
        }
      });
    }
  });
});

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
