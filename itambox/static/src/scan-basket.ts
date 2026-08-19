/**
 * ITAMbox — Scan basket for bulk check-in / bulk disposal.
 *
 * Accumulates assets into a basket via three inputs that all funnel through
 * `addByCode()`:
 *   - camera scan (mobile) — reuses the shared AssetScanner, kept open for rapid
 *     multi-scan;
 *   - USB barcode scanner (desktop) — emulates a keyboard, Enter-terminated;
 *   - manual typing.
 *
 * Each accepted code is resolved server-side (tenant-scoped, eligibility-checked)
 * and rendered as a row inside #scan-basket-form. Rows carry the hidden `pk`
 * inputs (and, in disposal mode, per-row `proceeds_<pk>` inputs) so a plain form
 * POST submits the whole batch. Success/failure reuse the audit beep events.
 */
import { AssetScanner } from './scanner';

interface ScanPayload {
  found: boolean;
  pk: number;
  label: string;
  asset_tag: string;
  serial: string;
  status: string;
  assigned_to: string;
  book_value: string | null;
  eligible: boolean;
  warning: string | null;
}

function beepOk(): void {
  document.dispatchEvent(new Event('playAuditSound'));
}
function beepFail(): void {
  document.dispatchEvent(new Event('playAuditFailSound'));
}

function showToast(message: string, variant: 'warning' | 'danger' = 'warning'): void {
  const container = document.getElementById('django-messages');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast show align-items-center text-bg-${variant} border-0 mb-2`;
  toast.setAttribute('role', 'alert');
  const row = document.createElement('div');
  row.className = 'd-flex';
  const body = document.createElement('div');
  body.className = 'toast-body';
  body.textContent = message; // textContent: scanned codes cannot inject markup
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'btn-close btn-close-white me-2 m-auto';
  close.setAttribute('data-bs-dismiss', 'toast');
  row.appendChild(body);
  row.appendChild(close);
  toast.appendChild(row);
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

let feedbackTimer = 0;

function scannerOverlayOpen(): boolean {
  const m = document.getElementById('basket-scanner-modal');
  return !!m && getComputedStyle(m).display !== 'none';
}

function showOverlayFeedback(message: string, state: 'ok' | 'warn' | 'fail'): void {
  const el = document.getElementById('basket-scan-feedback');
  if (!el) return;
  el.textContent = message;
  el.classList.remove('is-ok', 'is-warn', 'is-fail');
  el.classList.add('is-visible', `is-${state}`);
  if (feedbackTimer) clearTimeout(feedbackTimer);
  feedbackTimer = window.setTimeout(() => el.classList.remove('is-visible'), 1800);
}

/**
 * Route scan feedback. While the camera overlay is open, show an in-overlay
 * banner — the #django-messages toast container is at z-index 1100, below the
 * 9999 scanner overlay, so corner toasts would be hidden. Otherwise (USB/manual
 * entry, overlay closed) fall back to a toast.
 */
function notify(message: string, state: 'ok' | 'warn' | 'fail'): void {
  // Always write the in-overlay banner: it lives inside the 9999 overlay and is
  // only visible while the camera is open, so this needs no reliable open-check.
  // Add a corner toast ONLY when the overlay is closed (USB/manual entry), where
  // toasts at z-index 1100 are actually visible.
  showOverlayFeedback(message, state);
  if (!scannerOverlayOpen() && state !== 'ok') {
    showToast(message, state === 'fail' ? 'danger' : 'warning');
  }
}

function initScanBasket(): void {
  const root = document.getElementById('scan-basket-root');
  if (!root || root.dataset.basketInitialized) return;
  root.dataset.basketInitialized = 'true';

  const mode = root.dataset.mode || 'checkin';
  const resolveUrl = root.dataset.resolveUrl || '';

  const form = document.getElementById('scan-basket-form') as HTMLFormElement | null;
  const tbody = document.getElementById('scan-basket-rows');
  const template = document.getElementById('scan-basket-row-template') as HTMLTemplateElement | null;
  const input = document.getElementById('scan-basket-input') as HTMLInputElement | null;
  const countEl = document.getElementById('scan-basket-count');
  const emptyEl = document.getElementById('scan-basket-empty');
  const clearBtn = document.getElementById('scan-basket-clear') as HTMLButtonElement | null;
  const submitBtn = document.getElementById('scan-basket-submit') as HTMLButtonElement | null;
  if (!form || !tbody || !template) return;

  const tenantField = form.querySelector<HTMLSelectElement | HTMLInputElement>('[name="tenant"]');
  const tenantRequired = !!tenantField && tenantField.type !== 'hidden';
  const cameraBtn = document.getElementById('basket-open-scanner-btn') as HTMLButtonElement | null;

  const basket = new Set<number>();

  function updateState(): void {
    const count = basket.size;
    if (countEl) countEl.textContent = String(count);
    const overlayCount = document.getElementById('basket-scanner-count');
    if (overlayCount) overlayCount.textContent = String(count);
    if (emptyEl) emptyEl.classList.toggle('d-none', count !== 0);
    if (clearBtn) clearBtn.disabled = count === 0;
    const tenantSelected = !tenantRequired || !!tenantField?.value;
    if (input) input.disabled = !tenantSelected;
    if (cameraBtn) cameraBtn.disabled = !tenantSelected;
    if (submitBtn) submitBtn.disabled = count === 0 || !tenantSelected;
    document.querySelectorAll<HTMLElement>('.scan-basket-confirm-count').forEach((el) => {
      el.textContent = String(count);
    });
  }

  function flashRow(pk: number): void {
    const existing = tbody!.querySelector<HTMLElement>(`tr[data-pk="${pk}"]`);
    if (!existing) return;
    existing.classList.add('table-active');
    setTimeout(() => existing.classList.remove('table-active'), 700);
  }

  function renderRow(p: ScanPayload): void {
    const frag = template!.content.cloneNode(true) as DocumentFragment;
    const tr = frag.querySelector('tr') as HTMLElement;
    tr.dataset.pk = String(p.pk);

    const set = (field: string, value: string) => {
      const el = tr.querySelector<HTMLElement>(`[data-field="${field}"]`);
      if (el) el.textContent = value;
    };

    const pkInput = tr.querySelector<HTMLInputElement>('input[data-field="pk"]');
    if (pkInput) pkInput.value = String(p.pk);

    set('asset_tag', p.asset_tag || `#${p.pk}`);
    set('label', p.label);
    set('status', p.status || '—');
    set('assigned_to', p.assigned_to || '—');
    set('book_value', p.book_value || '—');

    const proceeds = tr.querySelector<HTMLInputElement>('input[data-field="proceeds"]');
    if (proceeds) {
      proceeds.name = `proceeds_${p.pk}`;
      // Book value is the depreciated accounting residual, NOT money received.
      // Show it only as a placeholder hint — never as the submitted value — so a
      // blank field correctly means "no proceeds" and dispose_asset() freezes the
      // residual into disposal_value itself.
      if (p.book_value) proceeds.placeholder = p.book_value;
    }

    const warn = tr.querySelector<HTMLElement>('[data-field="warning"]');
    if (warn && p.warning) {
      warn.textContent = p.warning;
      warn.hidden = false;
      tr.classList.add('table-warning');
    }

    tbody!.appendChild(tr);
  }

  let cameraScanner: AssetScanner | null = null;

  function isCurrentCameraAction(sessionGeneration: number | undefined): boolean {
    return sessionGeneration === undefined
      || (cameraScanner !== null && cameraScanner.isSessionCurrent(sessionGeneration));
  }

  /**
   * Resolve one code and fold it into the basket. Returns the round-trip so the
   * camera scanner can hold its gate open until this settles; USB and manual
   * entry ignore the result and stay ungated — one deliberate event per code.
   */
  function addByCode(code: string, sessionGeneration?: number): Promise<void> {
    const cleaned = (code || '').trim();
    if (!cleaned) return Promise.resolve();

    if (tenantRequired && !tenantField?.value) {
      notify(gettext('Select a target tenant before scanning.'), 'warn');
      return Promise.resolve();
    }

    const tenantQuery = tenantRequired || tenantField?.value
      ? `&tenant=${encodeURIComponent(tenantField?.value || '')}`
      : '';
    const url = `${resolveUrl}?code=${encodeURIComponent(cleaned)}&mode=${encodeURIComponent(mode)}${tenantQuery}`;
    return fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then((res) => {
        if (res.status === 403) throw new Error('forbidden');
        if (!res.ok) throw new Error('not_found');
        return res.json();
      })
      .then((data: ScanPayload) => {
        if (!isCurrentCameraAction(sessionGeneration)) return;
        if (!data.found) throw new Error('not_found');
        if (basket.has(data.pk)) {
          beepFail();
          flashRow(data.pk);
          notify(interpolate(gettext('Already in basket: %(code)s'), { code: cleaned }, true), 'warn');
          return;
        }
        basket.add(data.pk);
        renderRow(data);
        updateState();
        beepOk();
        notify(
          data.warning || interpolate(gettext('Added: %(label)s'), { label: data.asset_tag || data.label }, true),
          data.warning ? 'warn' : 'ok',
        );
      })
      .catch((err: Error) => {
        if (!isCurrentCameraAction(sessionGeneration)) return;
        // Resolves rather than re-throws: the failure is reported here, and the
        // camera gate re-arms on settle so the code can be presented again once
        // its duplicate window has passed. USB/manual retries are ungated.
        beepFail();
        if (err.message === 'forbidden') {
          notify(gettext('You do not have permission to do this.'), 'fail');
        } else {
          notify(interpolate(gettext('No asset matches: %(code)s'), { code: cleaned }, true), 'fail');
        }
      });
  }

  // ── USB scanner / manual entry: Enter terminates a scan ──
  if (input) {
    input.addEventListener('keydown', (event: KeyboardEvent) => {
      if (event.key !== 'Enter') return;
      event.preventDefault(); // never submit the form from the scan box
      const value = input.value;
      input.value = '';
      addByCode(value);
    });
  }

  if (tenantField && tenantRequired) {
    tenantField.addEventListener('change', () => {
      // Never carry assets scanned for a previous target tenant into a new
      // tenant-bound batch.
      basket.clear();
      tbody.innerHTML = '';
      updateState();
      input?.focus();
    });
  }

  // ── Camera scanner (kept open for rapid multi-scan) ──
  if (document.getElementById('basket-open-scanner-btn')) {
    cameraScanner = new AssetScanner({
      readerId: 'basket-scanner-reader',
      modalId: 'basket-scanner-modal',
      torchId: 'basket-toggle-torch-btn',
      openBtnId: 'basket-open-scanner-btn',
      closeBtnId: 'basket-close-scanner-btn',
      errorDivId: 'basket-scanner-error',
      onResult(code: string, sessionGeneration: number) {
        return addByCode(code, sessionGeneration);
      },
    });
  }

  // ── Remove a row ──
  tbody.addEventListener('click', (event) => {
    const btn = (event.target as HTMLElement).closest('.scan-basket-remove');
    if (!btn) return;
    const tr = btn.closest<HTMLElement>('tr.scan-basket-row');
    if (!tr) return;
    const pk = Number(tr.dataset.pk);
    basket.delete(pk);
    tr.remove();
    updateState();
  });

  // ── Clear all ──
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      basket.clear();
      tbody.innerHTML = '';
      updateState();
      if (input) input.focus();
    });
  }

  // ── Disposal confirm modal → submit the form ──
  const confirmSubmit = document.getElementById('scan-basket-confirm-submit');
  if (confirmSubmit) {
    confirmSubmit.addEventListener('click', () => {
      if (basket.size > 0) form.submit();
    });
  }

  // ── Seed from server-rendered selection (list-view checkbox seeding) ──
  const seedEl = document.getElementById('scan-seed-data');
  if (seedEl && seedEl.textContent) {
    try {
      const seeds = JSON.parse(seedEl.textContent) as ScanPayload[];
      seeds.forEach((p) => {
        if (p && p.pk && !basket.has(p.pk)) {
          basket.add(p.pk);
          renderRow(p);
        }
      });
    } catch (_e) {
      // malformed seed data — ignore, start with an empty basket
    }
  }

  updateState();
  if (input) input.focus();
}

document.addEventListener('DOMContentLoaded', initScanBasket);
document.body.addEventListener('htmx:afterSettle', initScanBasket);
