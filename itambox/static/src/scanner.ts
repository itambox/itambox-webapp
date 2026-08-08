import { ThrottledScanDispatcher } from './scan-gate';

interface AssetScannerConfig {
  /** ID of the div that html5-qrcode renders into */
  readerId: string;
  /** ID of the full-screen overlay element */
  modalId: string;
  /** ID of the torch toggle button (hidden until camera is running) */
  torchId: string;
  /** ID of the button that opens this scanner */
  openBtnId: string;
  /** ID of the close/cancel button */
  closeBtnId: string;
  /** ID of a div where a camera-error message can be shown (optional) */
  errorDivId?: string;
  /**
   * Called with the decoded string when a scan succeeds. Return the promise of
   * the domain action it starts: the scanner keeps the gate closed until that
   * promise settles, so duplicate frames cannot start the same action twice.
   */
  onResult: (code: string) => unknown;
  /** Override the quiet period before the same payload may fire again. */
  duplicateWindowMs?: number;
}

export class AssetScanner {
  private config: AssetScannerConfig;
  private readerElement: HTMLElement | null = null;
  private modal: HTMLElement | null = null;
  private torchBtn: HTMLElement | null = null;
  private errorDiv: HTMLElement | null = null;
  private html5QrcodeScanner: Html5Qrcode | null = null;
  private isTorchOn: boolean = false;
  /**
   * Throttles raw camera detections down to one domain action per scan. It lives
   * here, on the shared scanner, so every caller — global lookup, audit page and
   * both baskets — is gated once and identically. Manual and USB (keyboard-wedge)
   * entry never reaches this path and stays ungated.
   */
  private dispatcher: ThrottledScanDispatcher;

  constructor(config: AssetScannerConfig) {
    this.config = config;
    this.readerElement = document.getElementById(config.readerId);
    this.modal = document.getElementById(config.modalId);
    this.torchBtn = document.getElementById(config.torchId);
    this.errorDiv = config.errorDivId ? document.getElementById(config.errorDivId) : null;
    this.dispatcher = new ThrottledScanDispatcher(
      (code: string) => this.config.onResult(code),
      { duplicateWindowMs: config.duplicateWindowMs },
    );
    this.initEventListeners();
  }

  private initEventListeners(): void {
    const openBtn = document.getElementById(this.config.openBtnId);
    const closeBtn = document.getElementById(this.config.closeBtnId);

    if (openBtn) {
      openBtn.addEventListener('click', () => this.start());
    }
    if (closeBtn) {
      closeBtn.addEventListener('click', () => this.stop());
    }
    if (this.torchBtn) {
      this.torchBtn.addEventListener('click', () => this.toggleTorch());
    }
  }

  private showError(msg: string): void {
    if (this.errorDiv) {
      const msgEl = this.errorDiv.querySelector('[data-scanner-error-msg]') as HTMLElement | null;
      if (msgEl) msgEl.textContent = msg;
      this.errorDiv.classList.remove('d-none');
    }
  }

  private hideError(): void {
    if (this.errorDiv) {
      this.errorDiv.classList.add('d-none');
    }
  }

  public async start(): Promise<void> {
    if (!this.modal || !this.readerElement) {
      console.error('Scanner DOM elements missing for', this.config.readerId);
      return;
    }

    this.modal.classList.add('is-open');
    this.hideError();
    // A re-opened scanner starts clean: no wedged in-flight flag from a previous
    // session, and the payload scanned last time may be read again immediately.
    this.dispatcher.reset();

    // iOS WebKit only grants getUserMedia on HTTPS or literal localhost (not 127.0.0.1).
    // Detect this before calling .start() so the user sees a clear message.
    if (!window.isSecureContext) {
      this.showError(
        gettext('Camera unavailable. On iPhone/iPad, scanning requires HTTPS. Please use a hardware scanner or type the asset tag.')
      );
      return;
    }

    try {
      this.html5QrcodeScanner = new Html5Qrcode(this.config.readerId, {
        verbose: false,
        useBarCodeDetectorIfSupported: true,
      });

      const config = {
        fps: 15,
        qrbox: (width: number, height: number) => {
          const size = Math.min(width, height) * 0.75;
          return { width: Math.round(size), height: Math.round(size) };
        },
      };

      await this.html5QrcodeScanner.start(
        { facingMode: 'environment' },
        config,
        (decodedText: string) => {
          let raw = decodedText.trim();
          // Strip surrounding quotes
          if (raw.startsWith('"') && raw.endsWith('"')) {
            raw = raw.slice(1, -1).trim();
          }
          if (raw.startsWith("'") && raw.endsWith("'")) {
            raw = raw.slice(1, -1).trim();
          }

          // Normalize full-width colons
          raw = raw.replace(/：/g, ':');

          // Deep link: keep itambox://asset/<pk> intact for backend resolution
          if (raw.toLowerCase().startsWith('itambox://asset/')) {
            this.dispatcher.dispatch(raw);
            return;
          }

          if (raw.toLowerCase().startsWith('itambox://')) {
            raw = raw.slice(10).replace(/^\/+|\/+$/g, '').trim();
          } else if (raw.toLowerCase().startsWith('itambox:')) {
            raw = raw.slice(8).replace(/^\/+|\/+$/g, '').trim();
          }
          // Throttled: the decoder re-reports a payload that stays in view on
          // every frame, and each of those must not start its own action.
          this.dispatcher.dispatch(raw);
        },
        (_errorMessage: string) => {
          // Frame failures are normal while no code is in view — suppress
        }
      );


      try {
        const capabilities = this.html5QrcodeScanner.getRunningTrackCapabilities();
        if (capabilities && (capabilities as any).torch && this.torchBtn) {
          this.torchBtn.classList.remove('d-none');
          this.isTorchOn = false;
        }
      } catch (_capErr) {
        // Torch capability unavailable — not an error
      }

    } catch (err: any) {
      console.error('Camera/Scanner initialization failed:', err);
      const isPermissionDenied =
        err?.name === 'NotAllowedError' ||
        (typeof err?.message === 'string' && err.message.toLowerCase().includes('permission'));
      const msg = isPermissionDenied
        ? gettext('Camera permission denied. On iPhone/iPad check Settings › Safari › Camera, or use a hardware scanner / type the tag.')
        : gettext('Camera unavailable. Please use a hardware scanner or type the asset tag.');
      this.showError(msg);
      // Keep modal open so user sees the error and can cancel
    }
  }

  private async toggleTorch(): Promise<void> {
    if (this.html5QrcodeScanner && this.html5QrcodeScanner.isScanning) {
      try {
        this.isTorchOn = !this.isTorchOn;
        await this.html5QrcodeScanner.applyVideoConstraints({
          advanced: [{ torch: this.isTorchOn }],
        } as any);
      } catch (err) {
        console.warn('Torch toggle failed:', err);
      }
    }
  }

  public stop(): void {
    if (this.modal) {
      this.modal.classList.remove('is-open');
    }
    if (this.torchBtn) {
      this.torchBtn.classList.add('d-none');
    }
    this.isTorchOn = false;
    // Closing mid-action must not leave the gate busy for the next session.
    this.dispatcher.reset();

    if (this.html5QrcodeScanner) {
      // Capture the instance before awaiting stop(). A new scan may be opened
      // while the old camera promise is pending; the old callback must never
      // clear or null the replacement stored on this field.
      const scanner = this.html5QrcodeScanner;
      this.html5QrcodeScanner = null;
      if (scanner.isScanning) {
        scanner.stop()
          .then(() => scanner.clear())
          .catch(err => {
            console.error('Error stopping scanner:', err);
          });
      }
    }
  }
}

// ─── Audit page scanner (fills #barcode-scan-input, submits via HTMX form) ─────

function initAuditScanner(): void {
  const openBtn = document.getElementById('open-scanner-btn');
  if (!openBtn || openBtn.dataset.scannerInitialized) return;
  openBtn.dataset.scannerInitialized = 'true';
  const searchField = document.getElementById('barcode-scan-input') as HTMLInputElement | null;
  const instance = new AssetScanner({
    readerId: 'scanner-reader',
    modalId: 'scanner-modal',
    torchId: 'toggle-torch-btn',
    openBtnId: 'open-scanner-btn',
    closeBtnId: 'close-scanner-btn',
    errorDivId: 'scanner-error',
    onResult(code: string) {
      if (searchField) {
        searchField.value = code;
        searchField.dispatchEvent(new Event('input', { bubbles: true }));
        searchField.dispatchEvent(new Event('change', { bubbles: true }));
      }
      instance.stop();
    },
  });
}

// ─── Global scanner (resolves code → navigates to asset page) ───────────────

function initGlobalScanner(): void {
  const openBtn = document.getElementById('global-open-scanner-btn');
  if (!openBtn || openBtn.dataset.scannerInitialized) return;
  openBtn.dataset.scannerInitialized = 'true';

  const instance = new AssetScanner({
    readerId: 'global-scanner-reader',
    modalId: 'global-scanner-modal',
    torchId: 'global-toggle-torch-btn',
    openBtnId: 'global-open-scanner-btn',
    closeBtnId: 'global-close-scanner-btn',
    errorDivId: 'global-scanner-error',
    onResult(code: string) {
      // Returned so the scanner holds the gate until the lookup settles.
      return fetch('/scan/resolve/?code=' + encodeURIComponent(code))
        .then(res => {
          if (!res.ok) throw new Error('not_found');
          return res.json();
        })
        .then((data: { found: boolean; url?: string; label?: string }) => {
          if (data.found && data.url) {
            instance.stop();
            document.dispatchEvent(new Event('playAuditSound'));
            window.location.href = data.url;
          } else {
            document.dispatchEvent(new Event('playAuditFailSound'));
            showGlobalScanToast(interpolate(gettext('No asset matches: %(code)s'), { code }, true));
          }
        })
        .catch(() => {
          document.dispatchEvent(new Event('playAuditFailSound'));
          showGlobalScanToast(interpolate(gettext('No asset matches: %(code)s'), { code }, true));
        });
    },
  });
}

let globalScanFeedbackTimer: number | undefined;

function showGlobalScanToast(message: string): void {
  const overlayFeedback = document.getElementById('global-scanner-feedback');
  if (overlayFeedback) {
    if (globalScanFeedbackTimer !== undefined) {
      clearTimeout(globalScanFeedbackTimer);
    }
    overlayFeedback.setAttribute('role', 'alert');
    overlayFeedback.setAttribute('aria-live', 'assertive');
    overlayFeedback.setAttribute('aria-atomic', 'true');
    overlayFeedback.textContent = message;
    overlayFeedback.classList.remove('is-ok', 'is-fail');
    overlayFeedback.classList.add('is-visible', 'is-warn');
    globalScanFeedbackTimer = setTimeout(() => {
      overlayFeedback.classList.remove('is-visible', 'is-warn');
      overlayFeedback.textContent = '';
      globalScanFeedbackTimer = undefined;
    }, 4000);
    return;
  }

  // Keep the global toast fallback for pages that do not render the scanner overlay.
  const container = document.getElementById('django-messages');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'toast show align-items-center text-bg-warning border-0 mb-2';
  toast.setAttribute('role', 'alert');

  const row = document.createElement('div');
  row.className = 'd-flex';

  const body = document.createElement('div');
  body.className = 'toast-body';

  const icon = document.createElement('i');
  icon.className = 'mdi mdi-barcode-off me-2';

  // Use textContent so scanned codes cannot inject markup.
  const text = document.createElement('span');
  text.textContent = message;

  body.appendChild(icon);
  body.appendChild(text);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'btn-close btn-close-white me-2 m-auto';
  closeBtn.setAttribute('data-bs-dismiss', 'toast');

  row.appendChild(body);
  row.appendChild(closeBtn);
  toast.appendChild(row);
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ─── Bootstrap both scanners on initial load and HTMX partial swaps ─────────

function initScanners(): void {
  initAuditScanner();
  initGlobalScanner();
}

document.addEventListener('DOMContentLoaded', initScanners);
document.body.addEventListener('htmx:afterSettle', initScanners);
