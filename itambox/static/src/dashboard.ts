/**
 * ITAMbox Dashboard — GridStack integration with HTMX lifecycle management.
 *
 * Replaces inline script in dashboard.html. Handles:
 *  - GridStack initialization with Bootstrap grid fallback
 *  - Lock/unlock toggle
 *  - Save layout (with CSRF token fallback to cookie)
 *  - HTMX beforeSwap/afterSettle reinit
 *
 * Chart rendering delegates to the ECharts adapter in `dashboard-charts.ts`.
 */
import * as echarts from 'echarts/core';
import { BarChart, PieChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

import {
  createDashboardCharts,
  type DashboardCharts,
  type EChartsLib,
  type ResizeObserverClassLike,
} from './dashboard-charts';

// Register only the chart types and components the dashboard actually uses so
// the bundle stays tree-shaken instead of shipping the full ECharts distribution.
echarts.use([BarChart, PieChart, GridComponent, LegendComponent, TitleComponent, TooltipComponent, CanvasRenderer]);

const echartsLib: EChartsLib = {
  init: (element) => echarts.init(element),
  getInstanceByDom: (element) => echarts.getInstanceByDom(element),
  dispose: (element) => {
    echarts.dispose(element);
  },
};

(function () {
  const dashboardCharts: DashboardCharts = createDashboardCharts({
    echarts: echartsLib,
    document,
    resizeObserverClass:
      typeof ResizeObserver === 'undefined' ? null : (ResizeObserver as unknown as ResizeObserverClassLike),
  });
  let grid: GridStackInstance | null = null;
  let gsLoaded = false;

  function getCSRFToken(): string {
    return ITAMboxState.getCSRFToken();
  }

  function initGridStack(): void {
    try {
      const el = document.getElementById('dashboard-grid');
      if (!el) return;

      // Check DOM state instead of global window variables to prevent out-of-sync issues
      if (el.classList.contains('grid-stack') || (el as any).gridstack) {
        window.__gsInitialized = true;
        return;
      }

      // On small screens, skip GridStack entirely and keep the responsive
      // Bootstrap grid (col-12 / col-md-6) intact so widgets stack into at
      // most 1–2 columns instead of a cramped multi-column absolute layout.
      // Dragging/resizing isn't practical on touch screens anyway.
      if (window.innerWidth < 992) {
        el.classList.remove('grid-stack-loading');
        window.__gsInitialized = false;
        return;
      }

      // Collect Bootstrap cols BEFORE removing classes
      const cols: HTMLElement[] = [];
      Array.from(el.children).forEach(function (child) {
        if (child instanceof HTMLElement && child.className && /col-lg-\d+/.test(child.className))
          cols.push(child);
      });

      if (cols.length === 0) return;

      // Remove Bootstrap grid classes from container
      el.classList.remove('row', 'row-cards');

      cols.forEach(function (col, i) {
        const card = col.querySelector<HTMLElement>('.card');
        if (!card) return;

        // Read saved positions from data attributes
        const savedW = col.getAttribute('data-gs-w');
        const savedH = col.getAttribute('data-gs-h');
        const savedX = col.getAttribute('data-gs-x');
        const savedY = col.getAttribute('data-gs-y');

        // Remove ALL Bootstrap column classes
        col.className = col.className.replace(/col\S+/g, '').trim();
        col.classList.add('grid-stack-item');

        // Apply saved sizes (or defaults)
        col.setAttribute('gs-w', savedW || '4');
        col.setAttribute('gs-h', savedH || '2');
        col.setAttribute('gs-id', 'widget-' + i);

        // Apply saved position (falsy values = not set, autoposition)
        if (savedX) col.setAttribute('gs-x', savedX);
        if (savedY) col.setAttribute('gs-y', savedY);

        card.classList.add('grid-stack-item-content');
      });

      grid = GridStack.init(
        {
          column: 12,
          cellHeight: 100,
          margin: 8,
          disableDrag: true,
          disableResize: true,
          draggable: { handle: '.card-header' },
          resizable: { handles: 'e, se, s, sw, w' },
        },
        el,
      );

      // GridStack.init succeeded — mark as loaded
      gsLoaded = true;
      window.__gsInitialized = true;

      // Reveal the fully initialized dashboard grid smoothly
      el.classList.remove('grid-stack-loading');
    } catch (e) {
      console.warn('GridStack init error — using Bootstrap grid fallback:', e);
      // Restore Bootstrap classes so the fallback layout works
      const el = document.getElementById('dashboard-grid');
      if (el) {
        el.classList.remove('grid-stack-loading');
        if (!el.classList.contains('row')) {
          el.classList.add('row', 'row-cards');
          Array.from(el.children).forEach(function (child) {
            if (!(child instanceof HTMLElement)) return;
            const w = child.getAttribute('gs-w') || child.getAttribute('data-gs-w') || '4';
            child.classList.add('col-lg-' + w, 'col-md-6', 'col-12');
            child.classList.remove('grid-stack-item');
            const card = child.querySelector<HTMLElement>('.grid-stack-item-content');
            if (card) card.classList.remove('grid-stack-item-content');
          });
        }
      }
      window.__gsInitialized = false;
    }
  }

  function toggleLock(): void {
    if (!grid || !gsLoaded) return;
    const wasLocked = grid.opts.disableDrag;

    grid.enableMove(wasLocked);
    grid.enableResize(wasLocked);

    const isNowLocked = !wasLocked;

    const lockedEl = document.getElementById('dashboard-locked-controls');
    const unlockedEl = document.getElementById('dashboard-unlocked-controls');
    if (lockedEl) lockedEl.classList.toggle('d-none', !isNowLocked);
    if (unlockedEl) unlockedEl.classList.toggle('d-none', isNowLocked);

    document.querySelectorAll<HTMLElement>('#dashboard-grid .card').forEach(function (card) {
      card.classList.toggle('dashboard-card-editing', !isNowLocked);
    });
    document.querySelectorAll<HTMLElement>('.dashboard-manage-btn').forEach(function (btn) {
      btn.classList.toggle('d-none', isNowLocked);
    });
  }

  function saveLayout(): void {
    if (!grid || !gsLoaded) return;
    const items = grid.save(false);
    const widgets = items.map(function (item) {
      const id = item.id || '';
      const index = parseInt(id.replace('widget-', ''));
      return { index: isNaN(index) ? 0 : index, x: item.x || 0, y: item.y || 0, w: item.w || 4, h: item.h || 2 };
    });

    const gridEl = document.getElementById('dashboard-grid');
    const saveUrl = gridEl ? gridEl.getAttribute('data-save-url') || '/extras/dashboard/save-layout/' : '/extras/dashboard/save-layout/';

    fetch(saveUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRFToken(),
      },
      body: JSON.stringify({ widgets: widgets }),
    }).then(function (r) {
      if (!r.ok) return;
      const btn = document.getElementById('save-dashboard');
      if (!btn || btn.dataset['_saving'] === 'true') return;
      btn.dataset['_saving'] = 'true';
      const origHTML = btn.innerHTML;
      btn.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-check me-1" width="20" height="20" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M5 12l5 5l10 -10"/></svg>' + gettext('Saved');
      btn.classList.add('btn-success');
      btn.classList.remove('btn-primary');
      setTimeout(function () {
        btn.innerHTML = origHTML;
        btn.classList.remove('btn-success');
        btn.classList.add('btn-primary');
        btn.dataset['_saving'] = 'false';
      }, 1500);
    });
  }

  // --- Delegated click handler (survives DOM swaps) ---
  document.addEventListener('click', function (evt) {
    const btn = (evt.target as HTMLElement).closest('button');
    if (!btn) return;
    switch (btn.id) {
      case 'unlock-dashboard':
        toggleLock();
        break;
      case 'lock-dashboard':
        toggleLock();
        saveLayout();
        break;
      case 'save-dashboard':
        saveLayout();
        break;
    }
  });

  // Chart rendering lives in the ECharts adapter (dashboard-charts.ts), which
  // owns the init/update/resize/dispose lifecycle; this wrapper keeps the
  // GridStack and HTMX call sites unchanged.
  function initDashboardCharts(): void {
    dashboardCharts.initAll();
  }

  // Live theme switching: a single observer re-renders every chart for the
  // current color mode instead of accumulating one observer per chart.
  new MutationObserver(function () {
    dashboardCharts.updateTheme();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-bs-theme'] });

  // Delegated rename dashboard input listener to show check icon (replaces inline script)
  document.addEventListener('input', function (evt) {
    const target = evt.target as HTMLInputElement;
    if (target && target.name === 'name' && target.closest('#dashboard-modal-content')) {
      const form = target.closest('form');
      if (form) {
        const saveBtn = form.querySelector('.rename-save-btn');
        if (saveBtn) {
          saveBtn.classList.remove('d-none');
        }
      }
    }
  });

  // --- Init when DOM ready ---
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initGridStack();
      initDashboardCharts();
    });
  } else {
    initGridStack();
    initDashboardCharts();
  }

  // --- HTMX lifecycle: prevent history caching for the dashboard ---
  document.body.addEventListener('htmx:beforeHistorySave', function (evt: Event) {
    if (document.getElementById('dashboard-grid')) {
      evt.preventDefault();
    }
  });

  // --- HTMX lifecycle: destroy GridStack before navigating away ---
  document.body.addEventListener('htmx:beforeSwap', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const target = detail.target as HTMLElement | undefined;
    if (!target || !target.querySelector) return;
    if (target.querySelector('#dashboard-grid')) {
      grid = null;
      gsLoaded = false;
      window.__gsInitialized = false;
    }
    // Release chart instances inside the subtree that is about to be replaced.
    dashboardCharts.disposeWithin(target);
  });

  // Belt and braces: any element HTMX removes releases its chart instance and
  // ResizeObserver registration instead of leaking them.
  document.body.addEventListener('htmx:beforeCleanupElement', function (evt: Event) {
    const detail = (evt as CustomEvent).detail;
    const element = (detail.elt || detail.target) as HTMLElement | undefined;
    if (!element || !element.querySelectorAll) return;
    dashboardCharts.disposeWithin(element);
  });

  // --- HTMX lifecycle: reinitialize after history restore or content swap ---
  document.body.addEventListener('htmx:afterSettle', function () {
    const gridEl = document.getElementById('dashboard-grid');
    if (gridEl) {
      if (!gridEl.classList.contains('grid-stack')) {
        gsLoaded = false;
        initGridStack();
      }
      initDashboardCharts();
    } else {
      initDashboardCharts();
    }
  });
})();
