/**
 * ITAMbox dashboard charts — Apache ECharts adapter.
 *
 * Small, typed lifecycle adapter used by the dashboard widgets. It owns:
 *  - discovery of chart containers via their data-attribute contract,
 *  - option building for the doughnut / pie / bar widget variants,
 *  - instance lifecycle (init / update / resize / dispose) so HTMX swaps,
 *    GridStack resizes and theme switches never stack duplicate instances.
 *
 * The ECharts library is injected by the caller (dashboard.ts registers only
 * the chart types and components the dashboard uses), so this module keeps no
 * runtime dependency and stays unit-testable with a fake. Chart sizing comes
 * from the container CSS classes; this module never writes presentational
 * styles itself.
 */

export type ThemeMode = 'light' | 'dark';

export interface ChartDatum {
  name: string;
  count: number;
  color: string;
}

/** Minimal structural surface of an ECharts instance used by the adapter. */
export interface EChartsInstanceLike {
  setOption: (option: object) => void;
  resize: () => void;
}

/** Minimal structural surface of the ECharts module used by the adapter. */
export interface EChartsLib {
  init: (element: HTMLElement) => EChartsInstanceLike;
  getInstanceByDom: (element: HTMLElement) => EChartsInstanceLike | undefined;
  dispose: (element: HTMLElement) => void;
}

export interface ResizeObserverLike {
  observe: (element: Element) => void;
  unobserve: (element: Element) => void;
  disconnect: () => void;
}

export type ResizeObserverClassLike = new (callback: () => void) => ResizeObserverLike;

export interface DashboardChartsOptions {
  echarts: EChartsLib;
  document: Document;
  /**
   * ResizeObserver-compatible constructor. When omitted, a window resize
   * listener is used as fallback; without a view (tests), no resize source is
   * attached.
   */
  resizeObserverClass?: ResizeObserverClassLike | null;
}

export interface DashboardCharts {
  /** Initialize every chart container that does not have a live instance. */
  initAll: () => void;
  /** Dispose every instance this adapter owns. */
  disposeAll: () => void;
  /** Dispose instances inside an element that is about to be removed. */
  disposeWithin: (element: Element) => void;
  /** Re-render every live chart for the current color mode. */
  updateTheme: () => void;
}

const STATUS_LABELS_SELECTOR = '.itambox-status-labels-chart';
const ASSET_AGE_SELECTOR = '.itambox-asset-age-chart';
const CHART_SELECTOR = `${STATUS_LABELS_SELECTOR}, ${ASSET_AGE_SELECTOR}`;
const DEFAULT_DATUM_COLOR = '#626976';
const FALLBACK_FONT_FAMILY =
  'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

interface Palette {
  muted: string;
  strong: string;
  axisLine: string;
  splitLine: string;
  tooltipBackground: string;
  tooltipText: string;
}

const PALETTES: Record<ThemeMode, Palette> = {
  light: {
    muted: '#64748b',
    strong: '#0f172a',
    axisLine: '#e2e8f0',
    splitLine: '#eef2f7',
    tooltipBackground: '#ffffff',
    tooltipText: '#1e293b',
  },
  dark: {
    muted: '#94a3b8',
    strong: '#f8fafc',
    axisLine: '#334155',
    splitLine: '#243244',
    tooltipBackground: '#1f2937',
    tooltipText: '#f1f5f9',
  },
};

export function parseChartData(raw: string | null): ChartDatum[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((entry): entry is Record<string, unknown> => typeof entry === 'object' && entry !== null)
      .map((entry) => ({
        name: String(entry['name'] ?? ''),
        count: Number(entry['count'] ?? 0),
        color: typeof entry['color'] === 'string' && entry['color'] !== '' ? entry['color'] : DEFAULT_DATUM_COLOR,
      }));
  } catch (_error) {
    return [];
  }
}

function datumColor(datum: ChartDatum): string {
  return datum.color;
}

function buildTooltip(palette: Palette, fontFamily: string): object {
  return {
    trigger: 'item',
    backgroundColor: palette.tooltipBackground,
    borderColor: palette.axisLine,
    textStyle: { color: palette.tooltipText, fontFamily, fontSize: 12 },
    valueFormatter: (value: unknown) => interpolate(gettext('%(count)s assets'), { count: value }, true),
  };
}

function buildLegend(palette: Palette, fontFamily: string): object {
  return {
    show: true,
    bottom: 0,
    icon: 'circle',
    itemWidth: 8,
    itemHeight: 8,
    itemGap: 12,
    textStyle: { color: palette.muted, fontSize: 11, fontFamily },
  };
}

export function buildStatusLabelsOptions(
  data: ChartDatum[],
  chartType: string,
  theme: ThemeMode,
  fontFamily: string,
): object {
  const palette = PALETTES[theme];
  const names = data.map((datum) => datum.name);
  const base = {
    backgroundColor: 'transparent',
    animationDuration: 350,
    textStyle: { fontFamily },
    tooltip: buildTooltip(palette, fontFamily),
  };

  if (chartType === 'bar') {
    return {
      ...base,
      grid: { left: 4, right: 32, top: 4, bottom: 4, containLabel: true },
      legend: { show: false },
      xAxis: {
        type: 'value',
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: palette.splitLine } },
        axisLabel: { color: palette.muted, fontSize: 10, fontFamily },
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: names,
        axisLine: { lineStyle: { color: palette.axisLine } },
        axisTick: { show: false },
        axisLabel: { color: palette.muted, fontSize: 10, fontFamily },
      },
      series: [
        {
          name: gettext('Assets'),
          type: 'bar',
          barCategoryGap: '40%',
          data: data.map((datum) => ({
            value: datum.count,
            itemStyle: { color: datumColor(datum), borderRadius: 4 },
          })),
          label: { show: true, position: 'right', color: palette.muted, fontSize: 10, fontFamily, formatter: '{c}' },
        },
      ],
    };
  }

  const total = data.reduce((sum, datum) => sum + datum.count, 0);
  const isDoughnut = chartType !== 'pie';
  const option: Record<string, unknown> = {
    ...base,
    legend: buildLegend(palette, fontFamily),
    series: [
      {
        name: gettext('Assets'),
        type: 'pie',
        radius: isDoughnut ? ['42%', '62%'] : ['0%', '62%'],
        center: ['50%', '44%'],
        data: data.map((datum) => ({
          value: datum.count,
          name: datum.name,
          itemStyle: { color: datumColor(datum) },
        })),
        label: { show: true, position: 'inside', color: '#ffffff', fontSize: 11, fontFamily, formatter: '{c}' },
        labelLine: { show: false },
      },
    ],
  };

  if (isDoughnut) {
    option['title'] = {
      show: true,
      left: 'center',
      top: '33%',
      textAlign: 'center',
      text: String(total),
      subtext: gettext('Total'),
      textStyle: { color: palette.strong, fontSize: 16, fontWeight: 'bold', fontFamily },
      subtextStyle: { color: palette.muted, fontSize: 11, fontFamily },
    };
  }

  return option;
}

export function buildAssetAgeOptions(
  data: ChartDatum[],
  chartFormat: string,
  theme: ThemeMode,
  fontFamily: string,
): object {
  const palette = PALETTES[theme];
  const names = data.map((datum) => datum.name);
  const base = {
    backgroundColor: 'transparent',
    animationDuration: 300,
    textStyle: { fontFamily },
    tooltip: buildTooltip(palette, fontFamily),
  };

  if (chartFormat === 'pie') {
    return {
      ...base,
      legend: buildLegend(palette, fontFamily),
      series: [
        {
          name: gettext('Assets'),
          type: 'pie',
          radius: ['0%', '60%'],
          center: ['50%', '42%'],
          data: data.map((datum) => ({
            value: datum.count,
            name: datum.name,
            itemStyle: { color: datumColor(datum) },
          })),
          label: { show: true, position: 'inside', color: '#ffffff', fontSize: 11, fontFamily, formatter: '{c}' },
          labelLine: { show: false },
        },
      ],
    };
  }

  return {
    ...base,
    grid: { left: 4, right: 4, top: 18, bottom: 2, containLabel: true },
    legend: { show: false },
    xAxis: {
      type: 'category',
      data: names,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: palette.axisLine } },
      axisLabel: { color: palette.muted, fontSize: 10, fontFamily, interval: 0 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: palette.splitLine } },
      axisLabel: { color: palette.muted, fontSize: 10, fontFamily },
    },
    series: [
      {
        name: gettext('Assets'),
        type: 'bar',
        barCategoryGap: '45%',
        data: data.map((datum) => ({
          value: datum.count,
          itemStyle: { color: datumColor(datum), borderRadius: [4, 4, 0, 0] },
        })),
        label: { show: true, position: 'top', color: palette.muted, fontSize: 10, fontFamily, formatter: '{c}' },
      },
    ],
  };
}

function resolveFontFamily(doc: Document, element: Element): string {
  try {
    const computed = doc.defaultView?.getComputedStyle(element)?.fontFamily;
    return computed && computed.trim() !== '' ? computed : FALLBACK_FONT_FAMILY;
  } catch (_error) {
    return FALLBACK_FONT_FAMILY;
  }
}

export function createDashboardCharts(options: DashboardChartsOptions): DashboardCharts {
  const doc = options.document;
  const echarts = options.echarts;
  const ResizeObserverCtor = options.resizeObserverClass ?? null;
  const tracked = new Set<HTMLElement>();
  let resizeObserver: ResizeObserverLike | null = null;
  let resizeScheduled = false;

  function currentTheme(): ThemeMode {
    return doc.documentElement?.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';
  }

  function chartKind(container: HTMLElement): 'status-labels' | 'asset-age' | null {
    if (container.classList.contains('itambox-status-labels-chart')) return 'status-labels';
    if (container.classList.contains('itambox-asset-age-chart')) return 'asset-age';
    return null;
  }

  function buildOption(container: HTMLElement, data: ChartDatum[]): object {
    const theme = currentTheme();
    const fontFamily = resolveFontFamily(doc, container);
    if (chartKind(container) === 'status-labels') {
      return buildStatusLabelsOptions(data, container.getAttribute('data-chart-type') || 'doughnut', theme, fontFamily);
    }
    return buildAssetAgeOptions(data, container.getAttribute('data-chart-format') || 'bar', theme, fontFamily);
  }

  function renderEmptyState(container: HTMLElement): void {
    const empty = doc.createElement('div');
    empty.className = 'text-muted text-center py-4';
    empty.textContent = gettext('No assets assigned to active status labels.');
    container.replaceChildren(empty);
  }

  function resizeAll(): void {
    tracked.forEach((container) => {
      const instance = echarts.getInstanceByDom(container);
      if (instance) instance.resize();
    });
  }

  function scheduleResize(): void {
    if (resizeScheduled) return;
    resizeScheduled = true;
    const run = (): void => {
      resizeScheduled = false;
      resizeAll();
    };
    const view = doc.defaultView;
    if (view && typeof view.requestAnimationFrame === 'function') {
      view.requestAnimationFrame(run);
    } else {
      run();
    }
  }

  function ensureResizeSource(): void {
    if (resizeObserver) return;
    if (ResizeObserverCtor) {
      resizeObserver = new ResizeObserverCtor(scheduleResize);
      return;
    }
    doc.defaultView?.addEventListener('resize', scheduleResize);
  }

  function disposeContainer(container: HTMLElement): void {
    if (tracked.delete(container)) {
      resizeObserver?.unobserve(container);
    }
    if (echarts.getInstanceByDom(container)) {
      echarts.dispose(container);
    }
  }

  function initContainer(container: HTMLElement): void {
    if (!chartKind(container)) return;
    if (echarts.getInstanceByDom(container)) {
      tracked.add(container);
      return;
    }
    const data = parseChartData(container.getAttribute('data-chart-data'));
    if (data.length === 0) {
      if (chartKind(container) === 'status-labels') renderEmptyState(container);
      return;
    }
    const instance = echarts.init(container);
    instance.setOption(buildOption(container, data));
    tracked.add(container);
    ensureResizeSource();
    resizeObserver?.observe(container);
  }

  function initAll(): void {
    tracked.forEach((container) => {
      if (!container.isConnected) disposeContainer(container);
    });
    doc.querySelectorAll<HTMLElement>(CHART_SELECTOR).forEach(initContainer);
  }

  function disposeAll(): void {
    Array.from(tracked).forEach(disposeContainer);
    resizeObserver?.disconnect();
    resizeObserver = null;
  }

  function disposeWithin(element: Element): void {
    const scope = element as HTMLElement;
    if (typeof scope.matches === 'function' && scope.matches(CHART_SELECTOR)) {
      disposeContainer(scope);
    }
    if (typeof scope.querySelectorAll === 'function') {
      scope.querySelectorAll<HTMLElement>(CHART_SELECTOR).forEach(disposeContainer);
    }
  }

  function updateTheme(): void {
    tracked.forEach((container) => {
      const instance = echarts.getInstanceByDom(container);
      if (!instance) return;
      instance.setOption(buildOption(container, parseChartData(container.getAttribute('data-chart-data'))));
    });
  }

  return { initAll, disposeAll, disposeWithin, updateTheme };
}
