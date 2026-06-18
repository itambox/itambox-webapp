/**
 * ITAMbox Client-Side State — shared utility for config and CSRF access.
 *
 * Provides:
 *  - ITAMboxState.get(key) / .set(key, value) — typed localStorage wrapper
 *  - ITAMboxState.getUser() — reads data-itambox-user-id from <html>
 *  - ITAMboxState.getCSRFToken() — single-source CSRF token lookup
 */
// The ITAMboxUser / ITAMboxStateType interfaces and the ambient `ITAMboxState`
// global are declared in globals.d.ts; this module is the runtime implementation.
const ITAMboxStateImpl: ITAMboxStateType = (function () {
  function get<T>(key: string, defaultValue?: T): T | undefined {
    try {
      const raw = localStorage.getItem('itambox.' + key);
      return raw !== null ? (JSON.parse(raw) as T) : defaultValue;
    } catch (_e) {
      return defaultValue;
    }
  }

  function set(key: string, value: unknown): void {
    try {
      localStorage.setItem('itambox.' + key, JSON.stringify(value));
    } catch (_e) {
      // localStorage full or unavailable
    }
  }

  function getUser(): ITAMboxUser {
    const el = document.documentElement;
    return {
      id: el ? el.getAttribute('data-itambox-user-id') : null,
      name: el ? el.getAttribute('data-itambox-user-name') : null,
    };
  }

  function getCSRFToken(): string {
    const metaEl = document.querySelector<HTMLMetaElement>('meta[name=csrf-token]');
    if (metaEl && metaEl.content) return metaEl.content;
    const el = document.querySelector<HTMLInputElement>('[name=csrfmiddlewaretoken]');
    if (el && el.value) return el.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? match[1] : '';
  }

  return { get, set, getUser, getCSRFToken };
})();

(window as unknown as Record<string, unknown>).ITAMboxState = ITAMboxStateImpl;

// Global HTMX CSRF Token Integration
document.addEventListener('htmx:configRequest', (event: any) => {
  const token = (window as any).ITAMboxState?.getCSRFToken();
  if (token) {
    event.detail.headers['X-CSRFToken'] = token;
  }
});

// Global HTMX validation-error handling: the server answers failed form
// submissions with 422 + the re-rendered form fragment. HTMX refuses to swap
// non-2xx responses by default, so opt 422 in (and don't log it as an error).
document.addEventListener('htmx:beforeSwap', (event: any) => {
  if (event.detail?.xhr?.status === 422) {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});

