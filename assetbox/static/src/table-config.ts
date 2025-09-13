/**
 * AssetBox — Table Column Configuration Modal.
 *
 * Handles the table config modal interactions:
 *  - Move columns between available/selected lists
 *  - Reorder selected columns (up/down)
 *  - Apply configuration via PATCH to user-config API
 *  - Reset to defaults
 *
 * Uses HTMX afterSettle to re-bind after modal HTML is loaded into
 * #modal-placeholder (DOMContentLoaded fires before the modal exists).
 */
(function () {
  interface ConfigPayload {
    [key: string]: ConfigPayload | { columns: string[] };
  }

  let _boundBeforeSwap = false;

  function setupModal(modal: HTMLElement & { _tableConfigSetup?: boolean }): void {
    if (!modal || modal._tableConfigSetup) return;
    modal._tableConfigSetup = true;

    const form = modal.querySelector<HTMLFormElement>('form.userconfigform');
    if (!form) return;

    const available = modal.querySelector<HTMLSelectElement>('select.available-columns');
    const selected = modal.querySelector<HTMLSelectElement>('select.selected-columns');

    function moveOptions(source: HTMLSelectElement | null, dest: HTMLSelectElement | null): void {
      if (!source || !dest) return;
      Array.from(source.selectedOptions).forEach(function (opt) {
        dest.appendChild(opt);
        opt.selected = false;
      });
    }

    function moveOption(select: HTMLSelectElement | null, direction: 'up' | 'down'): void {
      if (!select) return;
      const opts = Array.from(select.selectedOptions);
      if (opts.length !== 1) return;
      const opt = opts[0];
      const idx = opt.index;
      if (direction === 'up' && idx > 0) {
        select.insertBefore(opt, select.options[idx - 1]);
      } else if (direction === 'down' && idx < select.options.length - 1) {
        select.insertBefore(opt, select.options[idx + 2]);
      }
    }

    function buildPayload(configRoot: string, selectedColumns: string[]): ConfigPayload {
      const payload: ConfigPayload = {};
      let current: ConfigPayload | { columns: string[] } = payload;
      const keys = configRoot.split('.');
      keys.forEach(function (key, i) {
        if (i === keys.length - 1) {
          (current as ConfigPayload)[key] = { columns: selectedColumns };
        } else {
          (current as ConfigPayload)[key] = (current as ConfigPayload)[key] || {};
          current = (current as ConfigPayload)[key];
        }
      });
      return payload;
    }

    function sendConfig(
      apiUrl: string,
      configRoot: string,
      selectedColumns: string[],
      csrfToken: string,
    ): Promise<unknown> {
      return fetch(apiUrl, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify(buildPayload(configRoot, selectedColumns)),
      }).then(function (resp) {
        if (!resp.ok) throw new Error('Failed to save configuration');
        return resp.json();
      });
    }

    const btnAdd = modal.querySelector<HTMLButtonElement>('#btn-add-cols');
    const btnRemove = modal.querySelector<HTMLButtonElement>('#btn-remove-cols');
    const btnUp = modal.querySelector<HTMLButtonElement>('#btn-cols-up');
    const btnDown = modal.querySelector<HTMLButtonElement>('#btn-cols-down');
    const btnApply = modal.querySelector<HTMLButtonElement>('#btn-apply-cols');
    const btnReset = modal.querySelector<HTMLButtonElement>('#btn-reset-cols');

    if (btnAdd) btnAdd.addEventListener('click', function () { moveOptions(available, selected); });
    if (btnRemove) btnRemove.addEventListener('click', function () { moveOptions(selected, available); });
    if (btnUp) btnUp.addEventListener('click', function () { moveOption(selected, 'up'); });
    if (btnDown) btnDown.addEventListener('click', function () { moveOption(selected, 'down'); });

    if (btnApply && selected)
      btnApply.addEventListener('click', function () {
        const cols = Array.from(selected.options).map(function (o) {
          return o.value;
        });
        const url = form.dataset.url;
        const root = form.dataset.configRoot;
        const tokenEl = form.querySelector<HTMLInputElement>('[name="csrfmiddlewaretoken"]');
        if (!url || !root || !tokenEl) return;
        const token = tokenEl.value;
        sendConfig(url, root, cols, token)
          .then(function () {
            const inst = bootstrap.Modal.getInstance(modal);
            if (inst) inst.hide();
            
            // Graceful AJAX swap instead of a hard reload
            if (typeof (window as any).refreshCurrentPage === 'function') {
              (window as any).refreshCurrentPage();
            } else {
              window.location.reload();
            }
          })
          .catch(function (err: Error) {
            alert('Error saving configuration: ' + err.message);
          });
      });

    if (btnReset)
      btnReset.addEventListener('click', function () {
        const url = form.dataset.url;
        const root = form.dataset.configRoot;
        const tokenEl = form.querySelector<HTMLInputElement>('[name="csrfmiddlewaretoken"]');
        if (!url || !root || !tokenEl) return;
        const token = tokenEl.value;
        sendConfig(url, root, [], token)
          .then(function () {
            const inst = bootstrap.Modal.getInstance(modal);
            if (inst) inst.hide();
            
            // Graceful AJAX swap instead of a hard reload
            if (typeof (window as any).refreshCurrentPage === 'function') {
              (window as any).refreshCurrentPage();
            } else {
              window.location.reload();
            }
          })
          .catch(function (err: Error) {
            alert('Error resetting configuration: ' + err.message);
          });
      });
  }

  // Re-run whenever new content settles in #modal-placeholder
  document.body.addEventListener('htmx:afterSettle', function () {
    const modal = document.getElementById('table-config-modal') as (HTMLElement & { _tableConfigSetup?: boolean }) | null;
    if (modal) setupModal(modal);
  });

  // Also run once on full page load
  document.addEventListener('DOMContentLoaded', function () {
    const modal = document.getElementById('table-config-modal') as (HTMLElement & { _tableConfigSetup?: boolean }) | null;
    if (modal) setupModal(modal);
  });

  // Clean up Bootstrap modal backdrop/scroll-lock when HTMX replaces
  // the modal placeholder content (e.g. opening a different modal)
  if (!_boundBeforeSwap) {
    _boundBeforeSwap = true;
    document.body.addEventListener('htmx:beforeSwap', function (evt: Event) {
      const detail = (evt as CustomEvent).detail;
      const placeholder = document.getElementById('modal-placeholder');
      if (!placeholder) return;
      if (detail.target && detail.target.id === 'modal-placeholder') {
        const existingModals = placeholder.querySelectorAll<HTMLElement>('.modal');
        existingModals.forEach(function (m) {
          const inst = bootstrap.Modal.getInstance(m);
          if (inst) {
            inst.hide();
          }
        });
      }
    });
  }
})();
