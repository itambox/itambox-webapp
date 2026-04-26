/**
 * ITAMbox Batch Actions — checkbox tracking, batch bar visibility, bulk assign.
 *
 * Handles select-all sync, top/bottom batch bar toggling, and bulk-assign
 * modal form wiring. Designed for object list tables inside #object-list-table-container.
 */
(function () {
  function updateBatchBar(): void {
    const bars = document.querySelectorAll<HTMLElement>('.batch-actions-bar');
    const deletePksInput = document.getElementById('bulk-delete-pks') as HTMLInputElement | null;
    const checkboxes = document.querySelectorAll<HTMLInputElement>(
      '#object-list-table-container input[type="checkbox"][name="pk"]',
    );
    const selected: HTMLInputElement[] = [];
    checkboxes.forEach(function (cb) {
      if (cb.checked) selected.push(cb);
    });
    const count = selected.length;

    bars.forEach(function (bar) {
      bar.classList.toggle('d-none', count === 0);
      const cnt = bar.querySelector<HTMLElement>('.fw-bold');
      if (cnt) cnt.textContent = count + ' selected';
    });

    if (deletePksInput) {
      deletePksInput.value = selected
        .map(function (cb) {
          return cb.value;
        })
        .join(',');
    }

    const selectAllCb = document.querySelector<HTMLInputElement>(
      '#object-list-table-container input[type="checkbox"][name="select_all"]',
    );
    if (selectAllCb) {
      const allCbs = document.querySelectorAll<HTMLInputElement>(
        '#object-list-table-container input[type="checkbox"][name="pk"]',
      );
      selectAllCb.checked = allCbs.length > 0 && selected.length === allCbs.length;
    }
  }

  document.addEventListener('change', function (event) {
    const target = event.target as HTMLInputElement;
    if (target.type !== 'checkbox') return;
    if (!target.closest('#object-list-table-container')) return;

    if (target.name === 'pk') {
      updateBatchBar();
    } else if (target.name === 'select_all') {
      const checkboxes = document.querySelectorAll<HTMLInputElement>(
        '#object-list-table-container input[type="checkbox"][name="pk"]',
      );
      checkboxes.forEach(function (cb) {
        cb.checked = target.checked;
      });
      updateBatchBar();
    }
  });

  document.body.addEventListener('htmx:afterSettle', function () {
    updateBatchBar();
  });

  // Initial run
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', updateBatchBar);
  } else {
    updateBatchBar();
  }

  // Bulk Assign modal submit handler (using event delegation to support dynamically loaded modals)
  document.addEventListener('submit', function (event) {
    const target = event.target as HTMLElement;
    const assignForm = target.closest<HTMLFormElement>('#bulk-assign-form');
    if (!assignForm) return;

    const checkboxes = document.querySelectorAll<HTMLInputElement>(
      '#object-list-table-container input[type="checkbox"][name="pk"]',
    );
    const pks: string[] = [];
    checkboxes.forEach(function (cb) {
      if (cb.checked) pks.push(cb.value);
    });

    if (pks.length === 0) {
      event.preventDefault();
      alert('No assets selected.');
      return;
    }

    let container = assignForm.querySelector<HTMLElement>('#bulk-assign-pks');
    if (!container) {
      container = document.createElement('div');
      container.id = 'bulk-assign-pks';
      assignForm.appendChild(container);
    }
    container.innerHTML = '';
    pks.forEach(function (pk) {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'pk';
      input.value = pk;
      container.appendChild(input);
    });
    // Let the event bubble naturally so HTMX or the browser processes the submit
  });
})();
