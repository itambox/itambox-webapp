/**
 * Dynamic behavior for the Beta module warning banner.
 * Managed entirely via event listeners to comply with strict CSP headers
 * and avoid issues during HTMX history navigation/restores.
 */

export function initBetaBanner() {
  const el = document.getElementById('beta-module-banner');
  if (el) {
    const maturity = el.dataset.maturity || 'beta';
    const capability = el.dataset.capability || 'legacy-module';
    const dismissalKey = `beta_banner_dismissed:${capability}`;
    if (maturity === 'beta' && sessionStorage.getItem(dismissalKey) === '1') {
      el.remove();
    } else {
      el.classList.remove('d-none');
      
      const closeBtn = el.querySelector('.btn-close');
      if (maturity === 'beta' && closeBtn && !closeBtn.getAttribute('data-listener-active')) {
        closeBtn.setAttribute('data-listener-active', 'true');
        closeBtn.addEventListener('click', () => {
          sessionStorage.setItem(dismissalKey, '1');
          el.remove();
        });
      }
    }
  }
}

// Run on page load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initBetaBanner);
} else {
  initBetaBanner();
}

// Run on HTMX content swaps
document.body?.addEventListener('htmx:afterSettle', initBetaBanner);
