/**
 * ITAMbox Dashboard — GridStack integration with HTMX lifecycle management.
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

  function initStatusLabelsCharts(): void {
    document.querySelectorAll('.itambox-status-labels-chart').forEach(function (container) {
      if ((container as any)._chart_init) return;
      (container as any)._chart_init = true;

      const chartType = container.getAttribute('data-chart-type') || 'doughnut';
      const dataRaw = container.getAttribute('data-chart-data') || '[]';
      let data: any[] = [];
      try {
        data = JSON.parse(dataRaw);
      } catch (e) {
        console.error('Failed to parse status label chart data:', e);
        return;
      }

      if (data.length === 0) {
        container.innerHTML = '<div class="text-muted text-center py-4">No assets assigned to active status labels.</div>';
        return;
      }

      const series = data.map((d: any) => d.count);
      const labels = data.map((d: any) => d.name);
      const colors = data.map((d: any) => d.color);

      const getThemeMode = () => document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';

      let options: any = {
        chart: {
          type: chartType === "doughnut" ? "donut" : chartType,
          height: 200,
          fontFamily: 'inherit',
          background: 'transparent',
          animations: {
            enabled: true,
            animateGradually: { enabled: true, delay: 150 },
            dynamicAnimation: { enabled: true, speed: 350 }
          },
          toolbar: { show: false }
        },
        theme: {
          mode: getThemeMode()
        },
        stroke: {
          show: true,
          width: 2,
          colors: getThemeMode() === 'dark' ? ['#1e293b'] : ['#ffffff']
        },
        colors: colors,
        labels: labels,
        legend: {
          show: true,
          position: 'bottom',
          fontSize: '11px',
          fontFamily: 'inherit',
          labels: {
            colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b'
          },
          markers: { width: 8, height: 8, radius: 4 }
        },
        dataLabels: {
          enabled: true,
          style: {
            fontSize: '11px',
            fontFamily: 'inherit',
            fontWeight: '600'
          },
          dropShadow: { enabled: false }
        },
        tooltip: {
          theme: getThemeMode(),
          y: {
            formatter: function(val: any) { return val + " assets"; }
          }
        }
      };

      if (chartType === 'bar') {
        options = {
          ...options,
          series: [{ name: "Assets", data: series }],
          chart: {
            ...options.chart,
            type: 'bar',
            height: 180
          },
          plotOptions: {
            bar: {
              borderRadius: 4,
              horizontal: true,
              barHeight: '60%',
              distributed: true
            }
          },
          dataLabels: {
            enabled: true,
            formatter: function(val: any) { return val; },
            style: { colors: ['#fff'] }
          },
          xaxis: {
            categories: labels,
            labels: {
              style: {
                colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b',
                fontFamily: 'inherit'
              }
            }
          },
          yaxis: {
            labels: {
              style: {
                colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b',
                fontFamily: 'inherit'
              }
            }
          },
          legend: { show: false }
        };
      } else {
        options.series = series;
        if (chartType === 'doughnut') {
          options.plotOptions = {
            pie: {
              donut: {
                size: '65%',
                labels: {
                  show: true,
                  name: {
                    show: true,
                    fontSize: '12px',
                    color: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b'
                  },
                  value: {
                    show: true,
                    fontSize: '16px',
                    fontWeight: 'bold',
                    color: getThemeMode() === 'dark' ? '#f8fafc' : '#0f172a',
                    formatter: function(val: any) { return val; }
                  },
                  total: {
                    show: true,
                    label: "Total",
                    color: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b',
                    formatter: function(w: any) {
                      return w.globals.seriesTotals.reduce((a: number, b: number) => a + b, 0);
                    }
                  }
                }
              }
            }
          };
        }
      }

      const chart = new (window as any).ApexCharts(container, options);
      chart.render();

      // Theme Switcher observer
      const observer = new MutationObserver(() => {
        const currentTheme = getThemeMode();
        chart.updateOptions({
          theme: { mode: currentTheme },
          stroke: {
            colors: currentTheme === 'dark' ? ['#1e293b'] : ['#ffffff']
          },
          legend: {
            labels: {
              colors: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
            }
          },
          tooltip: { theme: currentTheme }
        });
      });
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-bs-theme'] });
    });
  }

  function initAssetAgeCharts(): void {
    document.querySelectorAll('.itambox-asset-age-chart').forEach(function (container) {
      if ((container as any)._chart_init) return;
      (container as any)._chart_init = true;

      const chartFormat = container.getAttribute('data-chart-format') || 'bar';
      const dataRaw = container.getAttribute('data-chart-data') || '[]';
      let data: any[] = [];
      try {
        data = JSON.parse(dataRaw);
      } catch (e) {
        console.error('Failed to parse asset age chart data:', e);
        return;
      }

      const series = data.map((d: any) => d.count);
      const labels = data.map((d: any) => d.name);
      const colors = data.map((d: any) => d.color);

      const getThemeMode = () => document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';

      let options: any = {
        chart: {
          type: chartFormat,
          height: 180,
          fontFamily: 'inherit',
          background: 'transparent',
          animations: {
            enabled: true,
            animateGradually: { enabled: true, delay: 100 },
            dynamicAnimation: { enabled: true, speed: 300 }
          },
          toolbar: { show: false }
        },
        theme: {
          mode: getThemeMode()
        },
        stroke: {
          show: true,
          width: 2,
          colors: getThemeMode() === 'dark' ? ['#1e293b'] : ['#ffffff']
        },
        colors: colors,
        labels: labels,
        legend: {
          show: true,
          position: 'right',
          fontSize: '11px',
          fontFamily: 'inherit',
          labels: {
            colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b'
          },
          markers: { width: 8, height: 8, radius: 4 }
        },
        dataLabels: {
          enabled: true,
          style: {
            fontSize: '11px',
            fontFamily: 'inherit'
          },
          dropShadow: { enabled: false }
        },
        tooltip: {
          theme: getThemeMode(),
          y: {
            formatter: function(val: any) { return val + " assets"; }
          }
        }
      };

      if (chartFormat === 'bar') {
        options = {
          ...options,
          series: [{ name: "Assets", data: series }],
          chart: {
            ...options.chart,
            type: 'bar',
            height: 180
          },
          plotOptions: {
            bar: {
              borderRadius: 4,
              columnWidth: '55%',
              distributed: true
            }
          },
          dataLabels: {
            enabled: true,
            formatter: function(val: any) { return val; },
            style: { colors: ['#fff'] }
          },
          xaxis: {
            categories: labels,
            labels: {
              style: {
                colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b',
                fontFamily: 'inherit'
              }
            }
          },
          yaxis: {
            labels: {
              style: {
                colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b',
                fontFamily: 'inherit'
              }
            }
          },
          legend: { show: false }
        };
      } else {
        options.series = series;
        if (chartFormat === 'pie') {
          options.legend = {
            ...options.legend,
            position: 'bottom'
          };
        }
      }

      const chart = new (window as any).ApexCharts(container, options);
      chart.render();

      // Theme Switcher observer
      const observer = new MutationObserver(() => {
        const currentTheme = getThemeMode();
        chart.updateOptions({
          theme: { mode: currentTheme },
          stroke: {
            colors: currentTheme === 'dark' ? ['#1e293b'] : ['#ffffff']
          },
          legend: {
            labels: {
              colors: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
            }
          },
          tooltip: { theme: currentTheme }
        });
      });
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-bs-theme'] });
    });
  }

  function initTenantSpendCharts(): void {
    document.querySelectorAll('.itambox-tenant-spend-chart').forEach(function (container) {
      if ((container as any)._chart_init) return;
      (container as any)._chart_init = true;

      const chartType = container.getAttribute('data-chart-type') || 'bar';
      const currency = container.getAttribute('data-currency') || '€';
      const dataRaw = container.getAttribute('data-chart-data') || '[]';
      let data: any[] = [];
      try {
        data = JSON.parse(dataRaw);
      } catch (e) {
        console.error('Failed to parse tenant spend chart data:', e);
        return;
      }

      const seriesData = data.map((d: any) => d.total);
      const categories = data.map((d: any) => d.name);

      const getThemeMode = () => document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';

      let options: any = {
        chart: {
          type: chartType === "doughnut" ? "donut" : chartType,
          height: chartType === "bar" ? Math.max(160, 30 * data.length) : 200,
          fontFamily: 'inherit',
          background: 'transparent',
          animations: {
            enabled: true,
            animateGradually: { enabled: true, delay: 100 },
            dynamicAnimation: { enabled: true, speed: 300 }
          },
          toolbar: { show: false }
        },
        theme: {
          mode: getThemeMode()
        },
        stroke: {
          show: true,
          width: chartType === "bar" ? 0 : 2,
          colors: getThemeMode() === 'dark' ? ['#1e293b'] : ['#ffffff']
        },
        colors: [
          '#206bc4', '#7c3aed', '#2fb344', '#f59f00', 
          '#d63939', '#0ca678', '#f1c40f', '#17a2b8'
        ],
        dataLabels: {
          enabled: false
        },
        tooltip: {
          theme: getThemeMode(),
          y: {
            formatter: function(val: any) {
              return currency + " " + val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            }
          }
        },
        legend: {
          show: chartType !== "bar",
          position: 'bottom',
          fontSize: '11px',
          fontFamily: 'inherit',
          labels: {
            colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b'
          },
          markers: { width: 8, height: 8, radius: 4 }
        }
      };

      if (chartType === 'bar') {
        options = {
          ...options,
          series: [{
            name: "Spend",
            data: seriesData
          }],
          plotOptions: {
            bar: {
              borderRadius: 4,
              horizontal: true,
              barHeight: '70%',
              distributed: true
            }
          },
          xaxis: {
            categories: categories,
            labels: {
              style: {
                colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b',
                fontFamily: 'inherit'
              },
              formatter: function(val: any) {
                return currency + val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
              }
            }
          },
          yaxis: {
            labels: {
              style: {
                colors: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b',
                fontFamily: 'inherit'
              }
            }
          }
        };
      } else {
        options.series = seriesData;
        options.labels = categories;
        if (chartType === 'doughnut') {
          options.plotOptions = {
            pie: {
              donut: {
                size: '65%',
                labels: {
                  show: true,
                  name: {
                    show: true,
                    fontSize: '11px',
                    color: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b'
                  },
                  value: {
                    show: true,
                    fontSize: '15px',
                    fontWeight: 'bold',
                    color: getThemeMode() === 'dark' ? '#f8fafc' : '#0f172a',
                    formatter: function(val: any) { 
                      return currency + " " + Number(val).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
                    }
                  },
                  total: {
                    show: true,
                    label: "Total",
                    color: getThemeMode() === 'dark' ? '#94a3b8' : '#64748b',
                    formatter: function(w: any) {
                      const sum = w.globals.seriesTotals.reduce((a: number, b: number) => a + b, 0);
                      return currency + " " + sum.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
                    }
                  }
                }
              }
            }
          };
        }
      }

      const chart = new (window as any).ApexCharts(container, options);
      chart.render();

      // Theme Switcher observer
      const observer = new MutationObserver(() => {
        const currentTheme = getThemeMode();
        const updateObj: any = {
          theme: { mode: currentTheme },
          stroke: {
            colors: currentTheme === 'dark' ? ['#1e293b'] : ['#ffffff']
          },
          tooltip: { theme: currentTheme },
          legend: {
            labels: {
              colors: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
            }
          }
        };
        if (chartType === 'bar') {
          updateObj.xaxis = {
            labels: {
              style: {
                colors: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
              }
            }
          };
          updateObj.yaxis = {
            labels: {
              style: {
                colors: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
              }
            }
          };
        }
        chart.updateOptions(updateObj);
      });
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-bs-theme'] });
    });
  }

  function initDashboardCharts(): void {
    initStatusLabelsCharts();
    initAssetAgeCharts();
    initTenantSpendCharts();
  }

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
