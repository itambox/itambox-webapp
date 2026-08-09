/**
 * ITAMbox — camera scan gate.
 *
 * The camera decoder hands back roughly 15 detections per second, so a barcode
 * held in the viewfinder produces a burst of identical payloads. Without a gate
 * every frame starts its own domain action: duplicate rows, duplicate beeps and
 * a wall of toasts for what the user experienced as one scan.
 *
 * The gate applies two rules, in this order:
 *
 *   1. **In flight** — while an accepted scan has not settled, nothing else is
 *      accepted (not even a different payload). One accepted detection owns the
 *      scanner until its action finishes.
 *   2. **Duplicate window** — the payload that was last accepted stays
 *      suppressed until `duplicateWindowMs` has passed *since its action
 *      settled*. Anchoring on the settle instant (rather than on acceptance)
 *      keeps the window honest when the round-trip itself is slower than the
 *      window.
 *
 * A *different* payload is never held back by the previous payload's window, so
 * sweeping across several labels stays fast, and re-presenting a payload after
 * the window has elapsed is a deliberate re-scan — which is how a failed scan is
 * retried without reloading the page.
 *
 * This module is intentionally DOM-free: it is the throttle decision itself, so
 * it can be exercised at its real boundary with fake timers.
 * Manual and USB (keyboard-wedge) entry is one deliberate event per code and
 * never passes through this gate.
 */

/** Default quiet period for a repeat of the payload that was last accepted. */
export const DEFAULT_DUPLICATE_WINDOW_MS = 1500;

export interface ScanGateOptions {
  /** Quiet period for a repeat of the last accepted payload, in milliseconds. */
  duplicateWindowMs?: number;
  /** Clock source; injectable so callers can supply a deterministic one. */
  now?: () => number;
}

export class ScanGate {
  private readonly duplicateWindowMs: number;
  private readonly now: () => number;
  private busy = false;
  private lastCode: string | null = null;
  private lastSettledAt = 0;

  constructor(options: ScanGateOptions = {}) {
    this.duplicateWindowMs = options.duplicateWindowMs ?? DEFAULT_DUPLICATE_WINDOW_MS;
    this.now = options.now ?? (() => Date.now());
  }

  /** True while an accepted scan is still running. */
  public get isBusy(): boolean {
    return this.busy;
  }

  /**
   * Decide whether this detection should start a domain action. Accepting marks
   * the gate busy — the caller must pair every `true` with a `settle()`.
   */
  public accept(code: string): boolean {
    if (this.busy) return false;
    if (code === this.lastCode && this.now() - this.lastSettledAt < this.duplicateWindowMs) {
      return false;
    }
    this.busy = true;
    this.lastCode = code;
    this.lastSettledAt = this.now();
    return true;
  }

  /** Report that the accepted action finished — success or failure alike. */
  public settle(): void {
    this.busy = false;
    this.lastSettledAt = this.now();
  }

  /** Forget everything; a re-opened scanner may immediately re-read a payload. */
  public reset(): void {
    this.busy = false;
    this.lastCode = null;
    this.lastSettledAt = 0;
  }
}

/** A domain action for a scanned payload. Returning a promise defers `settle()`. */
export type ScanHandler = (code: string) => unknown;

function isThenable(value: unknown): value is PromiseLike<unknown> {
  return typeof (value as PromiseLike<unknown> | null)?.then === 'function';
}

/**
 * Feeds raw camera detections through a {@link ScanGate} into one domain action.
 *
 * When the handler returns a promise the gate stays closed until it settles, so
 * duplicate frames cannot start the same action twice; a rejected promise
 * re-arms the gate exactly like a fulfilled one, which is what makes a failed
 * scan retryable.
 *
 * `reset()` opens a new *generation*. An action started before the reset — the
 * user closed the overlay mid-lookup — still settles eventually, but its release
 * is bound to the generation it started in and is discarded once that generation
 * is gone. Without that binding a stale round-trip would clear the busy flag of
 * the scanner session that replaced it, letting duplicate frames start a second
 * action while the first is still running.
 */
export class ThrottledScanDispatcher {
  private readonly handler: ScanHandler;
  private readonly gate: ScanGate;
  private generation = 0;

  constructor(handler: ScanHandler, options: ScanGateOptions = {}) {
    this.handler = handler;
    this.gate = new ScanGate(options);
  }

  /** True while an accepted scan is still running. */
  public get isBusy(): boolean {
    return this.gate.isBusy;
  }

  public dispatch(code: string): void {
    const cleaned = (code || '').trim();
    if (!cleaned) return;
    if (!this.gate.accept(cleaned)) return;

    const generation = this.generation;
    const release = () => {
      // Ignore a settlement that belongs to a scanner session already closed.
      if (generation === this.generation) this.gate.settle();
    };

    let result: unknown;
    try {
      result = this.handler(cleaned);
    } catch (err) {
      // Release before propagating: a handler bug must not wedge the scanner.
      release();
      throw err;
    }

    if (isThenable(result)) {
      // Both arms release. The rejection is absorbed here on purpose — handlers
      // own their own error reporting, and an unhandled rejection from a camera
      // frame is noise, not a signal.
      result.then(release, release);
      return;
    }
    release();
  }

  /** Forget everything; used when the scanner is (re)opened or closed. */
  public reset(): void {
    this.generation += 1;
    this.gate.reset();
  }
}
