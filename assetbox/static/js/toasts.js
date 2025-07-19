/**
 * AssetBox Toast Messages — Bootstrap toast initialization and HTMX lifecycle.
 *
 * Handles:
 *  - Initial page-load toast rendering (#django-messages container)
 *  - HTMX OOB-targeted toast swaps
 *  - Inline toast elements inside swapped content
 *  - Custom showMessage event for dynamic toast creation
 *  - refreshCurrentPage() utility for post-modal page refresh
 *  - Custom event listeners (closeModalEvent, assetListUpdated, kitListUpdated)
 */
(function() {
    function initToastsInContainer(container) {
        var toasts = container.querySelectorAll('.toast');
        toasts.forEach(function(toastEl) {
            var toast = new bootstrap.Toast(toastEl);
            toast.show();
            toastEl.addEventListener('hidden.bs.toast', function() {
                toastEl.remove();
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        var container = document.getElementById('django-messages');
        if (container) initToastsInContainer(container);
    });

    document.body.addEventListener('htmx:afterSwap', function(evt) {
        if (evt.detail.target && evt.detail.target.id === 'django-messages') {
            initToastsInContainer(evt.detail.target);
            return;
        }
        if (evt.detail.elt) {
            initToastsInContainer(evt.detail.elt);
        }
    });

    window.refreshCurrentPage = function() {
        htmx.ajax('GET', window.location.pathname + window.location.search, {
            target: '#page-content-wrapper',
            swap: 'innerHTML'
        });
    };

    document.body.addEventListener('closeModalEvent', function() {
        var openModalEl = document.querySelector('.modal.show');
        if (openModalEl) {
            var modalInstance = bootstrap.Modal.getInstance(openModalEl) || new bootstrap.Modal(openModalEl);
            modalInstance.hide();
            window.refreshCurrentPage();
        }
    });

    document.body.addEventListener('assetListUpdated', function() {
        window.refreshCurrentPage();
    });

    document.body.addEventListener('kitListUpdated', function() {
        window.refreshCurrentPage();
    });

    document.body.addEventListener('showMessage', function(evt) {
        var detail = evt.detail;
        var message = detail.value ? detail.value.message : (detail.message || '');
        var level = detail.value ? detail.value.level : (detail.level || 'info');
        if (!message) return;

        var container = document.getElementById('django-messages');
        if (!container) return;

        var toastEl = document.createElement('div');
        var bgClass = level === 'success' ? 'bg-success' : level === 'danger' ? 'bg-danger' : 'bg-primary';
        toastEl.className = 'toast align-items-center text-white ' + bgClass + ' border-0';
        toastEl.setAttribute('role', 'alert');
        toastEl.setAttribute('aria-live', 'assertive');
        toastEl.setAttribute('aria-atomic', 'true');
        toastEl.innerHTML = '<div class="d-flex"><div class="toast-body">' + message + '</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>';
        container.appendChild(toastEl);

        var toast = new bootstrap.Toast(toastEl);
        toast.show();
        toastEl.addEventListener('hidden.bs.toast', function() {
            toastEl.remove();
        });
    });
})();
