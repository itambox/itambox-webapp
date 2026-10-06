/**
 * ITAMbox: shared feedback helpers for the scan baskets (bulk check-in /
 * disposal in scan-basket.ts and bulk audit in audit-basket.ts): success/failure
 * beeps, corner toasts and the in-overlay banner shown while the camera is open.
 */

export type FeedbackState = 'ok' | 'warn' | 'fail';

export function beepOk(): void {
  document.dispatchEvent(new Event('playAuditSound'));
}

export function beepFail(): void {
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

export interface FeedbackNotifier {
  (message: string, state: FeedbackState): void;
}

/**
 * Build a notifier bound to one basket's scanner modal and feedback banner.
 * While the camera overlay is open the message goes to the in-overlay banner (the
 * #django-messages toast container sits below the 9999 overlay); otherwise
 * (USB/manual entry) non-ok states also raise a corner toast.
 */
export function createNotifier(modalId: string, feedbackId: string): FeedbackNotifier {
  let feedbackTimer = 0;

  const overlayOpen = (): boolean => {
    const m = document.getElementById(modalId);
    return !!m && getComputedStyle(m).display !== 'none';
  };

  const overlayFeedback = (message: string, state: FeedbackState): void => {
    const el = document.getElementById(feedbackId);
    if (!el) return;
    el.textContent = message;
    el.classList.remove('is-ok', 'is-warn', 'is-fail');
    el.classList.add('is-visible', `is-${state}`);
    if (feedbackTimer) clearTimeout(feedbackTimer);
    feedbackTimer = window.setTimeout(() => el.classList.remove('is-visible'), 1800);
  };

  return (message, state) => {
    // The banner lives inside the overlay and is only visible while the camera is open,
    // so it is always written; the toast is added only when the overlay is closed.
    overlayFeedback(message, state);
    if (!overlayOpen() && state !== 'ok') {
      showToast(message, state === 'fail' ? 'danger' : 'warning');
    }
  };
}
