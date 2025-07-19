/**
 * AssetBox — Table Column Configuration Modal.
 *
 * Handles the table config modal interactions:
 *  - Move columns between available/selected lists
 *  - Reorder selected columns (up/down)
 *  - Apply configuration via PATCH to user-config API
 *  - Reset to defaults
 *
 * Extracted from core/includes/table_config_modal.html.
 */
(function() {
    document.addEventListener('DOMContentLoaded', function() {
        var modal = document.getElementById('table-config-modal');
        if (!modal) return;

        var form = modal.querySelector('form.userconfigform');
        if (!form) return;

        var available = modal.querySelector('select.available-columns');
        var selected = modal.querySelector('select.selected-columns');

        function moveOptions(source, dest) {
            Array.from(source.selectedOptions).forEach(function(opt) {
                dest.appendChild(opt);
                opt.selected = false;
            });
        }

        function moveOption(select, direction) {
            var opts = Array.from(select.selectedOptions);
            if (opts.length !== 1) return;
            var opt = opts[0];
            var idx = opt.index;
            if (direction === 'up' && idx > 0) {
                select.insertBefore(opt, select.options[idx - 1]);
            } else if (direction === 'down' && idx < select.options.length - 1) {
                select.insertBefore(opt, select.options[idx + 2]);
            }
        }

        function buildPayload(configRoot, selectedColumns) {
            var payload = {};
            var current = payload;
            var keys = configRoot.split('.');
            keys.forEach(function(key, i) {
                if (i === keys.length - 1) {
                    current[key] = { columns: selectedColumns };
                } else {
                    current[key] = current[key] || {};
                    current = current[key];
                }
            });
            return payload;
        }

        function sendConfig(apiUrl, configRoot, selectedColumns, csrfToken) {
            return fetch(apiUrl, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify(buildPayload(configRoot, selectedColumns))
            }).then(function(resp) {
                if (!resp.ok) throw new Error('Failed to save configuration');
                return resp.json();
            });
        }

        var btnAdd = modal.querySelector('#btn-add-cols');
        var btnRemove = modal.querySelector('#btn-remove-cols');
        var btnUp = modal.querySelector('#btn-cols-up');
        var btnDown = modal.querySelector('#btn-cols-down');
        var btnApply = modal.querySelector('#btn-apply-cols');
        var btnReset = modal.querySelector('#btn-reset-cols');

        if (btnAdd) btnAdd.addEventListener('click', function() { moveOptions(available, selected); });
        if (btnRemove) btnRemove.addEventListener('click', function() { moveOptions(selected, available); });
        if (btnUp) btnUp.addEventListener('click', function() { moveOption(selected, 'up'); });
        if (btnDown) btnDown.addEventListener('click', function() { moveOption(selected, 'down'); });

        if (btnApply) btnApply.addEventListener('click', function() {
            var cols = Array.from(selected.options).map(function(o) { return o.value; });
            var url = form.dataset.url;
            var root = form.dataset.configRoot;
            var token = form.querySelector('[name="csrfmiddlewaretoken"]').value;
            sendConfig(url, root, cols, token).then(function() {
                var inst = bootstrap.Modal.getInstance(modal);
                if (inst) inst.hide();
                window.location.reload();
            }).catch(function(err) {
                alert('Error saving configuration: ' + err.message);
            });
        });

        if (btnReset) btnReset.addEventListener('click', function() {
            var url = form.dataset.url;
            var root = form.dataset.configRoot;
            var token = form.querySelector('[name="csrfmiddlewaretoken"]').value;
            sendConfig(url, root, [], token).then(function() {
                var inst = bootstrap.Modal.getInstance(modal);
                if (inst) inst.hide();
                window.location.reload();
            }).catch(function(err) {
                alert('Error resetting configuration: ' + err.message);
            });
        });
    });
})();
