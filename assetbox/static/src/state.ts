/**
 * AssetBox Client-Side State — shared utility for config and CSRF access.
 *
 * Provides:
 *  - AssetBoxState.get(key) / .set(key, value) — typed localStorage wrapper
 *  - AssetBoxState.getUser() — reads data-assetbox-user-id from <html>
 *  - AssetBoxState.getCSRFToken() — single-source CSRF token lookup
 */
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

const AssetBoxState: AssetBoxStateType = (function () {
  function get<T>(key: string, defaultValue?: T): T | undefined {
    try {
      const raw = localStorage.getItem('assetbox.' + key);
      return raw !== null ? (JSON.parse(raw) as T) : defaultValue;
    } catch (_e) {
      return defaultValue;
    }
  }

  function set(key: string, value: unknown): void {
    try {
      localStorage.setItem('assetbox.' + key, JSON.stringify(value));
    } catch (_e) {
      // localStorage full or unavailable
    }
  }

  function getUser(): AssetBoxUser {
    const el = document.documentElement;
    return {
      id: el ? el.getAttribute('data-assetbox-user-id') : null,
      name: el ? el.getAttribute('data-assetbox-user-name') : null,
    };
  }

  function getCSRFToken(): string {
    const el = document.querySelector<HTMLInputElement>('[name=csrfmiddlewaretoken]');
    if (el && el.value) return el.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? match[1] : '';
  }

  return { get, set, getUser, getCSRFToken };
})();

(window as unknown as Record<string, unknown>).AssetBoxState = AssetBoxState;
