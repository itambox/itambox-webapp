/**
 * ITAMbox — Asset Audit Verification sound and animation handler.
 *
 * Listens for the 'playAuditSound' custom event dispatched by the server
 * HX-Trigger header after a successful physical audit verification.
 *
 * Handles:
 *  - Playing the embedded audio cue on success
 *  - Spring animation on the Verify Physical Presence button
 */
(function () {
  if (window._itamboxAuditReady) return;
  window._itamboxAuditReady = true;

  function playBeep(frequency: number, duration: number, type?: OscillatorType): void {
    try {
      const AudioCtx = (window.AudioContext || (window as any).webkitAudioContext);
      if (!AudioCtx) return;
      const context = new AudioCtx();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = type || 'sine';
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.08, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + duration);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + duration);
    } catch (e) {
      console.warn("Web Audio beep failed", e);
    }
  }

  function handleAuditSuccess(): void {
    // Play synthesizer beep
    playBeep(1000, 0.10);

    // Play fallback audio element if present
    const audio = document.getElementById('audit-success-audio') as HTMLAudioElement | null;
    if (audio) {
      audio.play().catch(function (_e) {
        // Suppress warning if browser blocks autoplay
      });
    }

    // Spring tactile animation on the verify button
    const btn = document.getElementById('audit-verify-btn');
    if (btn) {
      btn.style.transform = 'scale(0.95)';
      setTimeout(function () {
        btn.style.transform = 'scale(1.05)';
        setTimeout(function () {
          btn.style.transform = 'scale(1)';
        }, 100);
      }, 80);
    }
  }

  function handleAuditFail(): void {
    playBeep(180, 0.15, 'triangle');
    setTimeout(function () {
      playBeep(180, 0.15, 'triangle');
    }, 150);
  }

  function focusBarcodeScanInput() {
    const scanInput = document.getElementById("barcode-scan-input");
    if (scanInput) {
      scanInput.focus();
    }
  }

  document.addEventListener('playAuditSound', handleAuditSuccess);
  document.addEventListener('playAuditFailSound', handleAuditFail);

  document.addEventListener("DOMContentLoaded", focusBarcodeScanInput);
  document.body.addEventListener("htmx:afterSettle", focusBarcodeScanInput);
})();


