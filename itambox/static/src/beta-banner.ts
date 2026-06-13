/**
 * Dynamic behavior for the Beta module warning banner.
 * Managed entirely via event listeners to comply with strict CSP headers
 * and avoid issues during HTMX history navigation/restores.
 */

function initBetaBanner() {
  const el = document.getElementById('beta-module-banner');
  if (el) {
    if (sessionStorage.getItem('beta_banner_dismissed') === '1') {
      el.remove();
    } else {
      el.style.removeProperty('display');
      
      const closeBtn = el.querySelector('.btn-close');
      if (closeBtn && !closeBtn.getAttribute('data-listener-active')) {
        closeBtn.setAttribute('data-listener-active', 'true');
        closeBtn.addEventListener('click', () => {
          sessionStorage.setItem('beta_banner_dismissed', '1');
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
document.body.addEventListener('htmx:afterSettle', initBetaBanner);
