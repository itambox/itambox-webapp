/**
 * ITAMbox — Bulk Receive "apply to all" helper.
 *
 * Wires the #btn-apply-bulk-fill button on the bulk-receive formset page so the
 * bulk-fill values are copied into every formset row.
 *
 * Lives in the bundle (not an inline <script>) so it works after a boosted
 * navigation: an inline <script> rendered inside the swapped #page-content-wrapper
 * carries the boosted request's CSP nonce, which never matches the original
 * document's policy — so the browser blocks it (script-src-elem) and the button
 * would silently do nothing.
 */
(function () {
  function valueOf(id: string): string {
    const el = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
    return el ? el.value : '';
  }

  function setSelect(name: string, value: string): void {
    const select = document.querySelector<HTMLSelectElement>(`[name="${name}"]`);
    if (!select) return;
    const ts = (select as any).tomselect;
    if (ts) {
      ts.setValue(value);
    } else {
      select.value = value;
    }
  }

  function setInput(name: string, value: string): void {
    const input = document.querySelector<HTMLInputElement>(`[name="${name}"]`);
    if (input) input.value = value;
  }

  function applyBulkFill(): void {
    const statusVal = valueOf('bulk-fill-status');
    const locationVal = valueOf('bulk-fill-location');
    const supplierVal = valueOf('bulk-fill-supplier');
    const orderNumberVal = valueOf('bulk-fill-order-number').trim();
    const purchaseCostVal = valueOf('bulk-fill-purchase-cost').trim();
    const purchaseDateVal = valueOf('bulk-fill-purchase-date');

    const totalEl = document.getElementById('id_form-TOTAL_FORMS') as HTMLInputElement | null;
    if (!totalEl) return;
    const totalForms = parseInt(totalEl.value, 10);
    if (!Number.isFinite(totalForms)) return;

    for (let i = 0; i < totalForms; i++) {
      if (statusVal) setSelect(`form-${i}-status`, statusVal);
      if (locationVal) setSelect(`form-${i}-location`, locationVal);
      if (supplierVal) setSelect(`form-${i}-supplier`, supplierVal);
      if (orderNumberVal !== '') setInput(`form-${i}-order_number`, orderNumberVal);
      if (purchaseCostVal !== '') setInput(`form-${i}-purchase_cost`, purchaseCostVal);
      if (purchaseDateVal) setInput(`form-${i}-purchase_date`, purchaseDateVal);
    }
  }

  function init(): void {
    const applyBtn = document.getElementById('btn-apply-bulk-fill');
    if (!applyBtn || (applyBtn as any)._bulkFillWired) return;
    (applyBtn as any)._bulkFillWired = true;
    applyBtn.addEventListener('click', applyBulkFill);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Re-wire after a boosted/HTMX swap brings the page into #page-content-wrapper.
  document.body.addEventListener('htmx:afterSettle', init);
})();
