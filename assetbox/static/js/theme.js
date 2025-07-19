/**
 * AssetBox Theme Toggle — dark/light mode switching with localStorage persistence.
 *
 * Sets data-bs-theme on <html> based on stored preference or OS preference.
 * Toggle buttons use the .color-mode-toggle CSS class.
 *
 * NOTE: For FOUC prevention, a minimal inline script in <head> reads
 * localStorage before any CSS loads. This module handles the toggle UI.
 */
(function() {
    var THEME_KEY = 'assetbox.theme';

    function setMode(mode) {
        document.documentElement.setAttribute('data-bs-theme', mode);
        try { localStorage.setItem(THEME_KEY, mode); } catch(e) {}
    }

    function initMode() {
        var initialMode = 'light';
        try {
            var storedTheme = localStorage.getItem(THEME_KEY);
            var preferDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

            if (storedTheme) {
                initialMode = storedTheme;
            } else if (preferDark) {
                initialMode = 'dark';
            }
        } catch (error) {
            // localStorage unavailable — stick with default
        }
        return initialMode;
    }

    document.addEventListener('DOMContentLoaded', function() {
        var initialTheme = initMode();
        setMode(initialTheme);

        document.addEventListener('click', function(event) {
            var toggleButton = event.target.closest('.color-mode-toggle');
            if (toggleButton) {
                event.preventDefault();
                var currentTheme = document.documentElement.getAttribute('data-bs-theme');
                var newTheme = currentTheme === 'light' ? 'dark' : 'light';
                setMode(newTheme);
            }
        });
    });
})();
