/**
 * AssetBox — Tom Select integration for FK/M2M fields.
 *
 * Auto-initializes Tom Select on any <select> element with the
 * data-tom-select attribute, providing searchable dropdowns.
 *
 * Usage in templates:
 *   <select name="manufacturer_id" data-tom-select>
 *     <option value="">---------</option>
 *     {% for obj in objects %}<option value="{{ obj.pk }}">{{ obj }}</option>{% endfor %}
 *   </select>
 *
 * For dynamically loaded options via HTMX, use:
 *   data-tom-select-url="{% url 'api:...' %}" data-tom-select-value-field="id" data-tom-select-label-field="name"
 */
(function() {
    function initTomSelect(el) {
        var url = el.getAttribute('data-tom-select-url');
        var options = {
            plugins: ['dropdown_input'],
            create: false,
            render: {
                no_results: function() { return '<div class="no-results">No results found</div>'; }
            }
        };

        if (url) {
            options.valueField = el.getAttribute('data-tom-select-value-field') || 'id';
            options.labelField = el.getAttribute('data-tom-select-label-field') || 'name';
            options.searchField = options.labelField;
            options.load = function(query, callback) {
                if (!query.length) return callback();
                var headers = { 'Accept': 'application/json' };
                var token = AssetBoxState ? AssetBoxState.getCSRFToken() : '';
                if (token) headers['X-CSRFToken'] = token;
                fetch(url + '?q=' + encodeURIComponent(query), { headers: headers })
                    .then(function(response) { return response.json(); })
                    .then(function(json) { callback(json.results || json); })
                    .catch(function() { callback(); });
            };
        }

        if (el.multiple) {
            options.plugins.push('remove_button');
        }

        return new TomSelect(el, options);
    }

    function initAll() {
        document.querySelectorAll('select[data-tom-select]').forEach(function(sel) {
            if (sel.tomselect) return;
            initTomSelect(sel);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }

    // Re-init after HTMX content swaps
    document.body.addEventListener('htmx:afterSettle', function() {
        initAll();
    });
})();
