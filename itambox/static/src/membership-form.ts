/**
 * ITAMbox — Membership "Add member" form toggles.
 *
 * Progressive-disclosure for the unified grant flow (organization
 * MembershipForm):
 *   1. Who-radio: "Existing user" shows the user select, "New user" shows the
 *      inline email/first/last fields.
 *   2. Where-block: the managed-coverage refinement (.managed-refinement) only
 *      shows while the "Managed tenants" reach checkbox is checked; within it,
 *      the tenant-group picker and the specific-tenants picker follow the
 *      selected coverage.
 *
 * Purely cosmetic: hidden inputs still POST, and the form's server-side clean()
 * clears the unselected who-side and re-validates every reach/refinement combo.
 * Follows the permission-matrix.ts / form-toggles.ts init pattern
 * (DOMContentLoaded + htmx:afterSettle + delegated change events).
 */
(function () {
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

  function syncScope(form: HTMLElement) {
    const scope = form.querySelector('select[name="managed_scope"]') as HTMLSelectElement | null;
    if (!scope) return;
    toggleWrapper(form, 'div_id_scope_group', scope.value === 'tenant_group');
    toggleWrapper(form, 'div_id_assigned_tenants', scope.value === 'explicit');
  }

  function syncReach(form: HTMLElement) {
    const managed = form.querySelector('input[name="reach_managed"]') as HTMLInputElement | null;
    const refinement = form.querySelector('.managed-refinement') as HTMLElement | null;
    if (!managed || !refinement) return;
    refinement.style.display = managed.checked ? '' : 'none';
    if (managed.checked) syncScope(form);
  }

  function membershipForms(): HTMLElement[] {
    // Anchor on the form's own fields rather than a page id so the toggles also
    // work if the form is ever swapped in via HTMX.
    return Array.from(document.querySelectorAll('form')).filter(
      f => f.querySelector('input[name="who"]') || f.querySelector('input[name="reach_managed"]'),
    ) as HTMLElement[];
  }

  function initAll() {
    membershipForms().forEach(form => {
      syncWho(form);
      syncReach(form);
    });
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
    if (!form) return;
    if (name === 'who') syncWho(form);
    if (name === 'reach_managed') syncReach(form);
    if (name === 'managed_scope') syncScope(form);
  });
})();
