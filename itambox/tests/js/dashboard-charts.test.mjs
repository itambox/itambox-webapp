import assert from 'node:assert/strict';
import test from 'node:test';

// The i18n helpers are provided globally by the application bundle.
globalThis.gettext = (message) => message;
globalThis.interpolate = (message, context) => message.replace('%(count)s', String(context.count));

class FakeClassList {
  #values = new Set();

  add(...names) {
    names.forEach((name) => this.#values.add(name));
  }

  contains(name) {
    return this.#values.has(name);
  }
}

const selectorClassNames = (selector) =>
  selector
    .split(',')
    .map((part) => part.trim().replace(/^\./, ''))
    .filter((name) => name !== '');

class FakeElement {
  constructor(classes = []) {
    this.classList = new FakeClassList();
    classes.forEach((name) => this.classList.add(name));
    this.attributes = new Map();
    this.children = [];
    this.connected = true;
    this.replacedChildren = [];
  }

  set className(value) {
    this.classList = new FakeClassList();
    String(value)
      .split(/\s+/)
      .filter(Boolean)
      .forEach((name) => this.classList.add(name));
  }

  get isConnected() {
    return this.connected;
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  appendChild(child) {
    this.children.push(child);
    child.parent = this;
    return child;
  }

  matches(selector) {
    return selectorClassNames(selector).some((name) => this.classList.contains(name));
  }

  descendants() {
    return this.children.flatMap((child) => [child, ...child.descendants()]);
  }

  querySelectorAll(selector) {
    return this.descendants().filter((element) => element.matches(selector));
  }

  replaceChildren(...nodes) {
    this.replacedChildren = nodes;
    this.children = [...nodes];
  }
}

function createFakeDocument() {
  const roots = [];
  const documentElement = new FakeElement();
  const document = {
    documentElement,
    defaultView: {
      requestAnimationFrame: (callback) => callback(),
      addEventListener() {},
      getComputedStyle: () => ({ fontFamily: 'Inter' }),
    },
    createElement: () => new FakeElement(),
    querySelectorAll: (selector) =>
      roots.flatMap((root) => [root, ...root.descendants()]).filter((element) => element.matches(selector)),
    roots,
  };
  return document;
}

function createFakeEcharts() {
  const instances = new Map();
  const calls = { init: 0, dispose: 0 };
  const lib = {
    init(element) {
      calls.init += 1;
      const instance = {
        element,
        options: [],
        resizes: 0,
        setOption(option) {
          this.options.push(option);
        },
        resize() {
          this.resizes += 1;
        },
      };
      instances.set(element, instance);
      return instance;
    },
    getInstanceByDom(element) {
      return instances.get(element) ?? undefined;
    },
    dispose(element) {
      calls.dispose += 1;
      instances.delete(element);
    },
  };
  return { lib, instances, calls };
}

class FakeResizeObserver {
  static instances = [];

  constructor(callback) {
    this.callback = callback;
    this.observed = new Set();
    this.disconnected = false;
    FakeResizeObserver.instances.push(this);
  }

  observe(element) {
    this.observed.add(element);
  }

  unobserve(element) {
    this.observed.delete(element);
  }

  disconnect() {
    this.disconnected = true;
    this.observed.clear();
  }
}

const { parseChartData, buildStatusLabelsOptions, buildAssetAgeOptions, axisRotationForWidth, createDashboardCharts } =
  await import('./.build/dashboard-charts.mjs');

const STATUS_DATA = [
  { name: 'In Use', count: 12, color: '#206bc4' },
  { name: 'In Storage', count: 5, color: '#f59f00' },
];

function statusContainer(data = STATUS_DATA, chartType = 'doughnut') {
  const container = new FakeElement(['itambox-status-labels-chart']);
  container.setAttribute('data-chart-type', chartType);
  container.setAttribute('data-chart-data', JSON.stringify(data));
  return container;
}

function ageContainer(data = [{ name: '< 1 Year', count: 8, color: '#2fb344' }], chartFormat = 'bar') {
  const container = new FakeElement(['itambox-asset-age-chart']);
  container.setAttribute('data-chart-format', chartFormat);
  container.setAttribute('data-chart-data', JSON.stringify(data));
  return container;
}

function makeAdapter(document) {
  FakeResizeObserver.instances = [];
  const echarts = createFakeEcharts();
  const adapter = createDashboardCharts({
    echarts: echarts.lib,
    document,
    resizeObserverClass: FakeResizeObserver,
  });
  return { adapter, echarts };
}

test('parseChartData tolerates invalid payloads and normalizes entries', () => {
  assert.deepEqual(parseChartData(null), []);
  assert.deepEqual(parseChartData(''), []);
  assert.deepEqual(parseChartData('not json'), []);
  assert.deepEqual(parseChartData('{"name": "x"}'), []);
  assert.deepEqual(parseChartData('[]'), []);
  assert.deepEqual(parseChartData('[{"name": "A", "count": 3, "color": "#111111"}]'), [
    { name: 'A', count: 3, color: '#111111' },
  ]);
  assert.deepEqual(parseChartData('[{"name": "A"}]'), [{ name: 'A', count: 0, color: '#626976' }]);
});

test('status labels doughnut keeps data, colors, legend, total and localized tooltip values', () => {
  const option = buildStatusLabelsOptions(STATUS_DATA, 'doughnut', 'light', 'Inter');
  assert.equal(option.series[0].type, 'pie');
  assert.deepEqual(option.series[0].radius, ['42%', '62%']);
  assert.deepEqual(
    option.series[0].data.map((entry) => [entry.name, entry.value, entry.itemStyle.color]),
    [
      ['In Use', 12, '#206bc4'],
      ['In Storage', 5, '#f59f00'],
    ],
  );
  assert.equal(option.legend.show, true);
  assert.equal(option.title.text, '17');
  assert.equal(option.title.subtext, 'Total');
  assert.equal(option.tooltip.valueFormatter(12), '12 assets');
});

test('status labels pie and bar variants keep their shape and categories', () => {
  const pie = buildStatusLabelsOptions(STATUS_DATA, 'pie', 'dark', 'Inter');
  assert.deepEqual(pie.series[0].radius, ['0%', '62%']);
  assert.equal(pie.title, undefined);

  const bar = buildStatusLabelsOptions(STATUS_DATA, 'bar', 'dark', 'Inter');
  assert.equal(bar.series[0].type, 'bar');
  assert.deepEqual(bar.yAxis.data, ['In Use', 'In Storage']);
  assert.deepEqual(
    bar.series[0].data.map((entry) => entry.itemStyle.color),
    ['#206bc4', '#f59f00'],
  );
  assert.equal(bar.legend.show, false);
});

test('asset age options support bar and pie formats', () => {
  const data = [
    { name: '< 1 Year', count: 4, color: '#2fb344' },
    { name: '> 3 Years', count: 1, color: '#d63939' },
  ];
  const bar = buildAssetAgeOptions(data, 'bar', 'light', 'Inter');
  assert.equal(bar.series[0].type, 'bar');
  assert.deepEqual(bar.xAxis.data, ['< 1 Year', '> 3 Years']);
  assert.equal(bar.legend.show, false);

  const pie = buildAssetAgeOptions(data, 'pie', 'light', 'Inter');
  assert.equal(pie.series[0].type, 'pie');
  assert.equal(pie.legend.show, true);
  assert.deepEqual(
    pie.series[0].data.map((entry) => entry.itemStyle.color),
    ['#2fb344', '#d63939'],
  );
});

test('repeated initialization never stacks duplicate instances', () => {
  const document = createFakeDocument();
  const container = statusContainer();
  document.roots.push(container);
  const { adapter, echarts } = makeAdapter(document);

  adapter.initAll();
  adapter.initAll();
  adapter.initAll();

  assert.equal(echarts.calls.init, 1);
  assert.equal(echarts.instances.size, 1);
  assert.equal(FakeResizeObserver.instances.length, 1);
  assert.equal(FakeResizeObserver.instances[0].observed.size, 1);
});

test('empty datasets render the localized empty state without an instance', () => {
  const document = createFakeDocument();
  const container = statusContainer([]);
  document.roots.push(container);
  const { adapter, echarts } = makeAdapter(document);

  adapter.initAll();

  assert.equal(echarts.calls.init, 0);
  assert.equal(String(container.replacedChildren[0].textContent), 'No assets assigned to active status labels.');
  assert.equal(container.replacedChildren[0].classList.contains('text-muted'), true);
});

test('theme updates re-render every live chart with the dark palette', () => {
  const document = createFakeDocument();
  const status = statusContainer();
  const age = ageContainer();
  document.roots.push(status, age);
  const { adapter, echarts } = makeAdapter(document);

  adapter.initAll();
  const statusInstance = echarts.instances.get(status);
  const ageInstance = echarts.instances.get(age);
  assert.equal(statusInstance.options.length, 1);
  assert.equal(statusInstance.options[0].legend.textStyle.color, '#64748b');

  document.documentElement.setAttribute('data-bs-theme', 'dark');
  adapter.updateTheme();

  assert.equal(statusInstance.options.length, 2);
  assert.equal(statusInstance.options[1].legend.textStyle.color, '#94a3b8');
  assert.equal(ageInstance.options.length, 2);
  assert.equal(ageInstance.options[1].textStyle.fontFamily, 'Inter');
});

test('container resize drives instance.resize exactly once per callback', () => {
  const document = createFakeDocument();
  const container = statusContainer();
  document.roots.push(container);
  const { adapter, echarts } = makeAdapter(document);

  adapter.initAll();
  const instance = echarts.instances.get(container);
  FakeResizeObserver.instances[0].callback();

  assert.equal(instance.resizes, 1);
  assert.equal(FakeResizeObserver.instances.length, 1, 'no additional ResizeObserver is created');
});

test('disposeWithin releases instances of a removed subtree and allows a fresh mount', () => {
  const document = createFakeDocument();
  const wrapper = new FakeElement();
  const container = statusContainer();
  wrapper.appendChild(container);
  document.roots.push(wrapper);
  const { adapter, echarts } = makeAdapter(document);

  adapter.initAll();
  assert.equal(echarts.instances.size, 1);

  adapter.disposeWithin(wrapper);
  assert.equal(echarts.calls.dispose, 1);
  assert.equal(echarts.instances.size, 0);
  assert.equal(FakeResizeObserver.instances[0].observed.size, 0, 'the observer registration is dropped');

  adapter.initAll();
  assert.equal(echarts.calls.init, 2, 'a re-mounted container gets a fresh instance');
});

test('disposeAll tears everything down and a later init rebuilds the observer', () => {
  const document = createFakeDocument();
  const container = statusContainer();
  document.roots.push(container);
  const { adapter, echarts } = makeAdapter(document);

  adapter.initAll();
  adapter.disposeAll();

  assert.equal(echarts.instances.size, 0);
  assert.equal(FakeResizeObserver.instances[0].disconnected, true);

  adapter.initAll();
  assert.equal(echarts.instances.size, 1);
  assert.equal(FakeResizeObserver.instances.length, 2, 'a new observer is created after teardown');
});

test('detached containers are pruned on the next initialization pass', () => {
  const document = createFakeDocument();
  const container = statusContainer();
  document.roots.push(container);
  const { adapter, echarts } = makeAdapter(document);

  adapter.initAll();
  container.connected = false;
  document.roots.length = 0; // the widget was removed from the document
  adapter.initAll();

  assert.equal(echarts.calls.dispose, 1);
  assert.equal(echarts.instances.size, 0);
});

test('chart option data keeps hostile label text as plain string data', () => {
  const hostile = [{ name: '<img src=x onerror=alert(1)>', count: 1, color: '#111111' }];
  const option = buildStatusLabelsOptions(hostile, 'doughnut', 'light', 'Inter');
  assert.equal(option.series[0].data[0].name, '<img src=x onerror=alert(1)>');
  assert.equal(option.title.text, '1');
});

test('axisRotationForWidth rotates narrow asset-age labels and keeps wide ones horizontal', () => {
  assert.equal(axisRotationForWidth(0), 0, 'unknown width stays horizontal');
  assert.equal(axisRotationForWidth(226), 40, 'narrow widget rotates the category labels');
  assert.equal(axisRotationForWidth(299), 40);
  assert.equal(axisRotationForWidth(300), 0, 'wide widget keeps the labels horizontal');
  assert.equal(axisRotationForWidth(640), 0);
});

test('asset-age bar options carry the rotation for narrow containers', () => {
  const data = [
    { name: '< 1 Year', count: 8, color: '#2fb344' },
    { name: '1 - 3 Years', count: 5, color: '#4299e1' },
  ];
  const narrow = buildAssetAgeOptions(data, 'bar', 'light', 'Inter', 226);
  assert.equal(narrow.xAxis.axisLabel.rotate, 40);
  assert.equal(narrow.xAxis.axisLabel.hideOverlap, true);
  const wide = buildAssetAgeOptions(data, 'bar', 'light', 'Inter', 640);
  assert.equal(wide.xAxis.axisLabel.rotate, 0);
  const implicit = buildAssetAgeOptions(data, 'bar', 'light', 'Inter');
  assert.equal(implicit.xAxis.axisLabel.rotate, 0);
  const pie = buildAssetAgeOptions(data, 'pie', 'light', 'Inter', 226);
  assert.equal(pie.xAxis, undefined, 'the pie variant has no category axis');
});

test('a resize that crosses the width threshold re-applies the label rotation', () => {
  const document = createFakeDocument();
  const container = ageContainer();
  container.clientWidth = 226;
  document.roots.push(container);
  const { adapter, echarts } = makeAdapter(document);

  adapter.initAll();
  const instance = echarts.instances.get(container);
  assert.equal(instance.options[0].xAxis.axisLabel.rotate, 40);

  container.clientWidth = 640;
  FakeResizeObserver.instances[0].callback();

  assert.equal(instance.resizes, 1);
  const last = instance.options[instance.options.length - 1];
  assert.equal(last.xAxis.axisLabel.rotate, 0, 'the widened widget drops the rotation');
});
