/**
 * ITAMbox — Object Detail Tab and Refresh Controller.
 */
(function () {
  function initTabs() {
    const urlParams = new URLSearchParams(window.location.search);
    const tabParam = urlParams.get('tab');
    if (tabParam) {
      let tabEl = document.querySelector(`a[href="#${tabParam}"]`) as HTMLElement | null;
      if (!tabEl) {
        tabEl = document.querySelector(`a[href="?tab=${tabParam}"]`) as HTMLElement | null;
      }
      if (!tabEl) {
        tabEl = document.querySelector(`a[hx-get="?tab=${tabParam}"]`) as HTMLElement | null;
      }
      if (tabEl) {
        try {
          const tab = (bootstrap as any).Tab.getOrCreateInstance(tabEl);
          tab.show();
        } catch (_e) {
          tabEl.click();
        }
      }
    }
  }

  document.body.addEventListener('shown.bs.tab', function (e: any) {
    if (!e.target) return;
    const targetHref = e.target.getAttribute('href');
    if (targetHref) {
      let tabName: string | null = null;
      if (targetHref.startsWith('#')) {
        tabName = targetHref.substring(1);
      } else if (targetHref.startsWith('?')) {
        const params = new URLSearchParams(targetHref);
        tabName = params.get('tab');
      }

      if (tabName) {
        const newUrl = new URL(window.location.href);
        if (tabName === 'details') {
          newUrl.searchParams.delete('tab');
        } else {
          newUrl.searchParams.set('tab', tabName);
        }
        if (newUrl.toString() !== window.location.href) {
          window.history.pushState({}, '', newUrl.toString());
        }
      }
    }
  });

  function refreshDetailsPage() {
    if (typeof htmx !== 'undefined') {
      htmx.ajax('GET', window.location.href, { target: 'body', swap: 'outerHTML' });
    } else {
      window.location.reload();
    }
  }

  document.body.addEventListener('tableRefreshRequired', refreshDetailsPage);
  document.body.addEventListener('licenseUpdated', refreshDetailsPage);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTabs);
  } else {
    initTabs();
  }
  document.body.addEventListener('htmx:afterSettle', initTabs);
})();
