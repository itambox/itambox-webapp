/**
 * AssetBox Theme Toggle — dark/light mode switching with localStorage persistence.
 *
 * Sets data-bs-theme on <html> based on stored preference or OS preference.
 * Toggle buttons use the .color-mode-toggle CSS class.
 *
 * NOTE: For FOUC prevention, a minimal inline script in <head> reads
 * localStorage before any CSS loads. This module handles the toggle UI.
 */
(function () {
  const THEME_KEY = 'assetbox.theme';

  function setMode(mode: string): void {
    document.documentElement.setAttribute('data-bs-theme', mode);
    try {
      localStorage.setItem(THEME_KEY, mode);
    } catch (_e) {
      /* unavailable */
    }
  }

  function initMode(): string {
    let initialMode = 'light';
    try {
      const storedTheme = localStorage.getItem(THEME_KEY);
      const preferDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      if (storedTheme) {
        initialMode = storedTheme;
      } else if (preferDark) {
        initialMode = 'dark';
      }
    } catch (_error) {
      // localStorage unavailable — stick with default
    }
    return initialMode;
  }

  document.addEventListener('DOMContentLoaded', function () {
    const initialTheme = initMode();
    setMode(initialTheme);

    document.addEventListener('click', function (event) {
      const toggleButton = (event.target as HTMLElement).closest('.color-mode-toggle');
      if (toggleButton) {
        event.preventDefault();
        const currentTheme = document.documentElement.getAttribute('data-bs-theme');
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        setMode(newTheme);
      }
    });
  });
})();
