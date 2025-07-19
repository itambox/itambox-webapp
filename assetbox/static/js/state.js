/**
 * AssetBox Client-Side State — shared utility for config and CSRF access.
 *
 * Provides:
 *  - AssetBoxState.get(key) / .set(key, value) — typed localStorage wrapper
 *  - AssetBoxState.getUser() — reads data-assetbox-user-id from <html>
 *  - AssetBoxState.getCSRFToken() — single-source CSRF token lookup
 */
var AssetBoxState = (function() {
    function get(key, defaultValue) {
        try {
            var raw = localStorage.getItem('assetbox.' + key);
            return raw !== null ? JSON.parse(raw) : defaultValue;
        } catch (e) {
            return defaultValue;
        }
    }

    function set(key, value) {
        try {
            localStorage.setItem('assetbox.' + key, JSON.stringify(value));
        } catch (e) {
            // localStorage full or unavailable
        }
    }

    function getUser() {
        var el = document.documentElement;
        return {
            id: el ? el.getAttribute('data-assetbox-user-id') : null,
            name: el ? el.getAttribute('data-assetbox-user-name') : null,
        };
    }

    function getCSRFToken() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el && el.value) return el.value;
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
        return match ? match[1] : '';
    }

    return {
        get: get,
        set: set,
        getUser: getUser,
        getCSRFToken: getCSRFToken
    };
})();
