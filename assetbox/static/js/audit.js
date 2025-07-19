/**
 * AssetBox — Asset Audit Verification sound and animation handler.
 *
 * Listens for the 'playAuditSound' custom event dispatched by the server
 * HX-Trigger header after a successful physical audit verification.
 *
 * Handles:
 *  - Playing the embedded audio cue on success
 *  - Spring animation on the Verify Physical Presence button
 */
(function() {
    if (window._assetboxAuditReady) return;
    window._assetboxAuditReady = true;

    function handleAuditSuccess() {
        var audio = document.getElementById('audit-success-audio');
        if (audio) {
            audio.play().catch(function(e) {
                console.log('Audio play blocked by browser autoplay restrictions: ', e);
            });
        }
        // Spring tactile animation on the verify button
        var btn = document.getElementById('audit-verify-btn');
        if (btn) {
            btn.style.transform = 'scale(0.95)';
            setTimeout(function() {
                btn.style.transform = 'scale(1.05)';
                setTimeout(function() {
                    btn.style.transform = 'scale(1)';
                }, 100);
            }, 80);
        }
    }

    document.body.addEventListener('playAuditSound', handleAuditSuccess);
})();
