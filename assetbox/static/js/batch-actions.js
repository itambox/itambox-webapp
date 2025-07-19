/**
 * AssetBox Batch Actions — checkbox tracking, batch bar visibility, bulk assign.
 *
 * Handles select-all sync, top/bottom batch bar toggling, and bulk-assign
 * modal form wiring. Designed for object list tables inside #object-list-table-container.
 */
(function() {
    function updateBatchBar() {
        var bars = document.querySelectorAll('.batch-actions-bar');
        var deletePksInput = document.getElementById('bulk-delete-pks');
        var checkboxes = document.querySelectorAll('#object-list-table-container input[type="checkbox"][name="pk"]');
        var selected = [];
        checkboxes.forEach(function(cb) { if (cb.checked) selected.push(cb); });
        var count = selected.length;

        bars.forEach(function(bar) {
            bar.classList.toggle('d-none', count === 0);
            var cnt = bar.querySelector('.fw-bold');
            if (cnt) cnt.textContent = count + ' selected';
        });

        if (deletePksInput) {
            deletePksInput.value = selected.map(function(cb) { return cb.value; }).join(',');
        }

        var selectAllCb = document.querySelector('#object-list-table-container input[type="checkbox"][name="select_all"]');
        if (selectAllCb) {
            var allCbs = document.querySelectorAll('#object-list-table-container input[type="checkbox"][name="pk"]');
            selectAllCb.checked = allCbs.length > 0 && selected.length === allCbs.length;
        }
    }

    document.addEventListener('change', function(event) {
        var target = event.target;
        if (target.type !== 'checkbox') return;
        if (!target.closest('#object-list-table-container')) return;

        if (target.name === 'pk') {
            updateBatchBar();
        } else if (target.name === 'select_all') {
            var checkboxes = document.querySelectorAll('#object-list-table-container input[type="checkbox"][name="pk"]');
            checkboxes.forEach(function(cb) { cb.checked = target.checked; });
            updateBatchBar();
        }
    });

    document.body.addEventListener('htmx:afterSettle', function() {
        updateBatchBar();
    });

    // Initial run
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', updateBatchBar);
    } else {
        updateBatchBar();
    }

    // Bulk Assign modal submit handler
    var assignForm = document.getElementById('bulk-assign-form');
    if (assignForm) {
        assignForm.addEventListener('submit', function(e) {
            e.preventDefault();
            var checkboxes = document.querySelectorAll('#object-list-table-container input[type="checkbox"][name="pk"]');
            var pks = [];
            checkboxes.forEach(function(cb) { if (cb.checked) pks.push(cb.value); });
            if (pks.length === 0) { alert('No assets selected.'); return; }

            var container = assignForm.querySelector('#bulk-assign-pks');
            if (!container) { container = document.createElement('div'); container.id = 'bulk-assign-pks'; assignForm.appendChild(container); }
            container.innerHTML = '';
            pks.forEach(function(pk) {
                var input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'pk';
                input.value = pk;
                container.appendChild(input);
            });
            assignForm.submit();
        });
    }
})();
