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

  function handleAuditSuccess(): void {
    const audio = document.getElementById('audit-success-audio') as HTMLAudioElement | null;
    if (audio) {
      audio.play().catch(function (_e) {
        console.warn('Audio play blocked by browser autoplay restrictions: ', _e);
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

  document.body.addEventListener('playAuditSound', handleAuditSuccess);
})();
