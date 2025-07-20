// Global type declarations for AssetBox

// HTMX
declare const htmx: {
  boost: () => void;
  process: (elt: Element) => void;
  trigger: (elt: Element, event: string, detail?: unknown) => void;
  find: (elt: Element, selector: string) => Element | null;
  ajax: (method: string, url: string, opts?: Record<string, unknown>) => void;
  on: (event: string, callback: (evt: Event) => void) => void;
};

// Bootstrap
declare const bootstrap: {
  Toast: {
    new (element: Element, options?: Record<string, unknown>): {
      show: () => void;
      hide: () => void;
      dispose: () => void;
    };
    getInstance: (element: Element) => { hide: () => void } | null;
    getOrCreateInstance: (element: Element) => { show: () => void; hide: () => void; _isShown?: boolean };
  };
  Modal: {
    new (element: Element, options?: Record<string, unknown>): {
      show: () => void;
      hide: () => void;
      dispose: () => void;
    };
    getInstance: (element: Element) => { hide: () => void } | null;
    getOrCreateInstance: (element: Element) => { show: () => void; hide: () => void; _isShown?: boolean };
  };
};

// TomSelect
interface TomSelectInstance {
  destroy: () => void;
}
interface TomSelectOptions {
  plugins?: string[];
  create?: boolean;
  render?: Record<string, () => { wrapper: string } | string>;
  valueField?: string;
  labelField?: string;
  searchField?: string;
  load?: (query: string, callback: (results?: unknown[]) => void) => void;
  [key: string]: unknown;
}
declare class TomSelect {
  constructor(element: HTMLSelectElement, options: TomSelectOptions);
  tomselect?: TomSelectInstance;
  destroy: () => void;
}

// GridStack
interface GridStackItem {
  id?: string;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
}
interface GridStackOptions {
  column: number;
  cellHeight: number;
  margin: number;
  disableDrag: boolean;
  disableResize: boolean;
  draggable: { handle: string };
  resizable: { handles: string };
}
interface GridStackInstance {
  opts: GridStackOptions;
  enableMove: (enable: boolean) => void;
  enableResize: (enable: boolean) => void;
  save: (includeStatic: boolean) => GridStackItem[];
  destroy: (removeDOM: boolean) => void;
  load: (items: GridStackItem[]) => void;
}
declare const GridStack: {
  init: (options: GridStackOptions, element?: HTMLElement) => GridStackInstance;
};

// AssetBox global state (created by state.ts)
interface AssetBoxUser {
  id: string | null;
  name: string | null;
}
interface AssetBoxStateType {
  get: <T>(key: string, defaultValue?: T) => T | undefined;
  set: (key: string, value: unknown) => void;
  getUser: () => AssetBoxUser;
  getCSRFToken: () => string;
}
declare const AssetBoxState: AssetBoxStateType;

// AssetBox global functions
declare function refreshCurrentPage(): void;
declare function toggleFilters(): void;
declare function initFiltersToggle(): void;

// Window extensions
interface Window {
  AssetBoxState?: AssetBoxStateType;
  refreshCurrentPage?: () => void;
  toggleFilters?: () => void;
  initFiltersToggle?: () => void;
  __gsInitialized?: boolean;
  _assetboxAuditReady?: boolean;
}
