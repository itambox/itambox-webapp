/**
 * ITAMbox — membership and user-group grant form interactivity.
 *
 * Progressive-disclosure + formset management for the unified, lossless grant
 * flow (organization MembershipForm and users.UserGroupForm):
 *   1. Who-radio: "Existing user" shows the user select, "New user" shows the
 *      inline email/first/last fields.
 *   2. Managed-tenants formsets: each row's coverage
 *      refinement (tenant-group vs specific-tenants picker) follows that row's
 *      "Coverage" (managed_scope) select. An "Add managed grant" button clones the
 *      <template> empty form and bumps TOTAL_FORMS; each row's remove button
 *      checks its Django DELETE box and collapses the row.
 *
 * Purely an enhancement: hidden inputs still POST, the DELETE checkbox stays
 * usable without JS, and the server-side clean()/formset re-validates every row.
 * Follows the permission-matrix.ts / form-toggles.ts init pattern
 * (DOMContentLoaded + htmx:afterSettle + delegated events).
 */
(function () {
  const MANAGED_PREFIX = 'managed';

  function toggleWrapper(root: ParentNode, id: string, show: boolean) {
    const el = root.querySelector('#' + id) as HTMLElement | null;
    if (el) el.style.display = show ? '' : 'none';
  }

  function syncWho(form: HTMLElement) {
    const checked = form.querySelector('input[name="who"]:checked') as HTMLInputElement | null;
    if (!checked) return; // edit form: no who-radio, user field stays visible
    const isNew = checked.value === 'new';
    toggleWrapper(form, 'div_id_user', !isNew);
    toggleWrapper(form, 'div_id_new_user_email', isNew);
    toggleWrapper(form, 'div_id_new_user_first_name', isNew);
    toggleWrapper(form, 'div_id_new_user_last_name', isNew);
  }

  function syncRowScope(row: HTMLElement) {
    const scope = row.querySelector('.managed-scope') as HTMLSelectElement | null;
    const group = row.querySelector('[data-scope-group]') as HTMLElement | null;
    const tenants = row.querySelector('[data-scope-tenants]') as HTMLElement | null;
    if (!scope) return;
    if (group) group.style.display = scope.value === 'tenant_group' ? '' : 'none';
    if (tenants) tenants.style.display = scope.value === 'explicit' ? '' : 'none';
  }

  function syncAllRows(scope: ParentNode) {
    scope.querySelectorAll('[data-managed-row]').forEach(r => syncRowScope(r as HTMLElement));
  }

  function totalFormsInput(fieldset: HTMLElement): HTMLInputElement | null {
    return fieldset.querySelector(
      `input[name="${MANAGED_PREFIX}-TOTAL_FORMS"]`,
    ) as HTMLInputElement | null;
  }

  function addManagedRow(fieldset: HTMLElement) {
    const total = totalFormsInput(fieldset);
    const tpl = fieldset.querySelector('#managed-empty-form') as HTMLTemplateElement | null;
    const container = fieldset.querySelector('#managed-formset-rows') as HTMLElement | null;
    if (!total || !tpl || !container) return;
    const index = parseInt(total.value, 10) || 0;
    const html = tpl.innerHTML.replace(/__prefix__/g, String(index));
    const wrapper = document.createElement('div');
    wrapper.innerHTML = html.trim();
    const row = wrapper.firstElementChild as HTMLElement | null;
    if (!row) return;
    container.appendChild(row);
    total.value = String(index + 1);
    syncRowScope(row);
  }

  function removeManagedRow(row: HTMLElement) {
    const del = row.querySelector('input[name$="-DELETE"]') as HTMLInputElement | null;
    if (del) {
      // Existing rows carry a Django DELETE field: mark + collapse so the server
      // reconciler revokes the grant. Brand-new (unsaved) rows have one too —
      // marking it simply makes the server ignore the row.
      del.checked = true;
      row.style.display = 'none';
    } else {
      row.remove();
    }
  }

  function initAll() {
    document.querySelectorAll('form').forEach(form => syncWho(form as HTMLElement));
    document.querySelectorAll('[data-managed-formset]').forEach(fs => syncAllRows(fs as HTMLElement));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
  document.body.addEventListener('htmx:afterSettle', initAll);

  document.body.addEventListener('change', (e) => {
    const target = e.target as HTMLElement | null;
    if (!target) return;
    const name = target.getAttribute('name');
    const form = target.closest('form') as HTMLElement | null;
    if (form && name === 'who') syncWho(form);
    if (target.classList.contains('managed-scope')) {
      const row = target.closest('[data-managed-row]') as HTMLElement | null;
      if (row) syncRowScope(row);
    }
  });

  document.body.addEventListener('click', (e) => {
    const target = e.target as HTMLElement | null;
    if (!target) return;
    const addBtn = target.closest('[data-add-managed-row]') as HTMLElement | null;
    if (addBtn) {
      const fieldset = addBtn.closest('[data-managed-formset]') as HTMLElement | null;
      if (fieldset) addManagedRow(fieldset);
      return;
    }
    const removeBtn = target.closest('[data-remove-managed-row]') as HTMLElement | null;
    if (removeBtn) {
      const row = removeBtn.closest('[data-managed-row]') as HTMLElement | null;
      if (row) removeManagedRow(row);
    }
  });
})();
