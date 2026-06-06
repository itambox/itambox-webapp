/**
 * ITAMbox Batch Actions — checkbox tracking, batch bar visibility, bulk assign.
 *
 * Handles select-all sync, top/bottom batch bar toggling, and bulk-assign
 * modal form wiring. Designed for object list tables inside #object-list-table-container.
 */
(function () {
  function updateBatchBar(): void {
    const bars = document.querySelectorAll<HTMLElement>('.batch-actions-bar');
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

  function initBulkEditSelectors(): void {
    const selectors = document.querySelectorAll<HTMLInputElement>('.bulk-edit-selector');
    selectors.forEach(function (cb) {
      const fieldName = cb.value;
      const container = document.getElementById('field_container_' + fieldName);
      if (container) {
        container.style.display = cb.checked ? '' : 'none';
      }
    });
  }

  document.addEventListener('change', function (event) {
    const target = event.target as HTMLInputElement;
    if (target.type !== 'checkbox') return;

    if (target.classList.contains('bulk-edit-selector')) {
      const fieldName = target.value;
      const container = document.getElementById('field_container_' + fieldName);
      if (container) {
        container.style.display = target.checked ? '' : 'none';
      }
      return;
    }

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
    initBulkEditSelectors();
  });

  // Initial run
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      updateBatchBar();
      initBulkEditSelectors();
    });
  } else {
    updateBatchBar();
    initBulkEditSelectors();
  }

  // Bulk Assign and Bulk Print modal submit handler (using event delegation to support dynamically loaded modals)
  document.addEventListener('submit', function (event) {
    const target = event.target as HTMLElement;
    const form = target.closest<HTMLFormElement>('#bulk-assign-form') || target.closest<HTMLFormElement>('#bulk-print-form');
    if (!form) return;

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

    const containerId = form.id === 'bulk-assign-form' ? 'bulk-assign-pks' : 'bulk-print-pks';
    let container = form.querySelector<HTMLElement>('#' + containerId);
    if (!container) {
      container = document.createElement('div');
      container.id = containerId;
      form.appendChild(container);
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

  // Delegated click handler for bulk delete/edit to align with strict CSP
  document.addEventListener('click', function (event) {
    const target = event.target as HTMLElement;

    // 1. Bulk Delete
    const deleteBtn = target.closest('.btn-bulk-delete');
    if (deleteBtn) {
      event.preventDefault();
      if (!confirm('Delete selected items? This cannot be undone.')) {
        return;
      }
      const form = document.getElementById('bulk-delete-form') as HTMLFormElement | null;
      if (form) {
        const checked = document.querySelectorAll<HTMLInputElement>(
          '#object-list-table-container input[type="checkbox"][name="pk"]:checked'
        );
        if (checked.length === 0) {
          alert('No items selected.');
          return;
        }
        let container = form.querySelector<HTMLElement>('#bulk-delete-pks-container');
        if (!container) {
          container = document.createElement('div');
          container.id = 'bulk-delete-pks-container';
          form.appendChild(container);
        }
        container.innerHTML = '';
        checked.forEach(function (cb) {
          const input = document.createElement('input');
          input.type = 'hidden';
          input.name = 'pk';
          input.value = cb.value;
          container.appendChild(input);
        });
        form.submit();
      }
      return;
    }

    // 2. Bulk Edit
    const editBtn = target.closest('.btn-bulk-edit');
    if (editBtn) {
      event.preventDefault();
      const form = document.getElementById('bulk-edit-form') as HTMLFormElement | null;
      if (form) {
        const checked = document.querySelectorAll<HTMLInputElement>(
          '#object-list-table-container input[type="checkbox"][name="pk"]:checked'
        );
        if (checked.length === 0) {
          alert('No items selected.');
          return;
        }
        let container = form.querySelector<HTMLElement>('#bulk-edit-pks-container');
        if (!container) {
          container = document.createElement('div');
          container.id = 'bulk-edit-pks-container';
          form.appendChild(container);
        }
        container.innerHTML = '';
        checked.forEach(function (cb) {
          const input = document.createElement('input');
          input.type = 'hidden';
          input.name = 'pk';
          input.value = cb.value;
          container.appendChild(input);
        });
        form.submit();
      }
      return;
    }
  });
})();
