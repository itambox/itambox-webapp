/**
 * AssetBox Dashboard — GridStack integration with HTMX lifecycle management.
 *
 * Replaces inline script in dashboard.html. Handles:
 *  - GridStack initialization with Bootstrap grid fallback
 *  - Lock/unlock toggle
 *  - Save layout (with CSRF token fallback to cookie)
 *  - HTMX beforeSwap/afterSettle reinit
 */
(function () {
  let grid: GridStackInstance | null = null;
  let gsLoaded = false;

  function getCSRFToken(): string {
    return AssetBoxState.getCSRFToken();
  }

  function initGridStack(): void {
    try {
      if (window.__gsInitialized) return;
      const el = document.getElementById('dashboard-grid');
      if (!el) return;

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
    } catch (e) {
      console.warn('GridStack init error — using Bootstrap grid fallback:', e);
      // Restore Bootstrap classes so the fallback layout works
      const el = document.getElementById('dashboard-grid');
      if (el && !el.classList.contains('row')) {
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
    if (lockedEl) lockedEl.style.display = isNowLocked ? '' : 'none';
    if (unlockedEl) unlockedEl.style.display = isNowLocked ? 'none' : '';

    document.querySelectorAll<HTMLElement>('#dashboard-grid .card').forEach(function (card) {
      card.style.outline = isNowLocked ? '' : '2px dashed var(--tblr-primary)';
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

    fetch('/extras/dashboard/save-layout/', {
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
        '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-check me-1" width="20" height="20" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M5 12l5 5l10 -10"/></svg>Saved';
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

  // --- Init when DOM ready ---
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGridStack);
  } else {
    initGridStack();
  }

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
  });

  // --- HTMX lifecycle: reinitialize after history restore or content swap ---
  document.body.addEventListener('htmx:afterSettle', function () {
    const gridEl = document.getElementById('dashboard-grid');
    if (!gridEl) return;
    if (!gridEl.classList.contains('grid-stack')) {
      window.__gsInitialized = false;
      gsLoaded = false;
      initGridStack();
    }
  });
})();
