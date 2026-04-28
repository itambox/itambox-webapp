/**
 * ITAMbox — Tom Select integration for FK/M2M fields.
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
 *   data-tom-select-url="{% url 'api:...' %}"
 *   data-tom-select-value-field="id"
 *   data-tom-select-label-field="name"
 */
(function () {
  interface TomSelectLoadResponse {
    results?: unknown[];
  }

  function initTomSelect(el: HTMLSelectElement): TomSelect | null {
    const url = el.getAttribute('data-tom-select-url');
    const plugins = ['dropdown_input'];
    if (el.multiple) {
      plugins.push('remove_button');
    } else {
      plugins.push('clear_button');
    }

    const options: TomSelectOptions = {
      plugins: plugins,
      create: false,
      allowEmptyOption: true,
      render: {
        no_results: function () {
          return '<div class="no-results">No results found</div>';
        } as unknown as () => { wrapper: string },
      },
    };

    if (url) {
      options.valueField = el.getAttribute('data-tom-select-value-field') || 'id';
      options.labelField = el.getAttribute('data-tom-select-label-field') || 'name';
      options.searchField = options.labelField as string;
      options.load = function (query: string, callback: (results?: unknown[]) => void) {
        if (!query.length) return callback();
        const headers: Record<string, string> = { Accept: 'application/json' };
        const token = ITAMboxState ? ITAMboxState.getCSRFToken() : '';
        if (token) headers['X-CSRFToken'] = token;
        fetch(url + '?q=' + encodeURIComponent(query), { headers })
          .then(function (response) {
            return response.json();
          })
          .then(function (json: TomSelectLoadResponse) {
            callback(json.results || (json as unknown as unknown[]));
          })
          .catch(function () {
            callback();
          });
      };
    }

    return new TomSelect(el, options);
  }

  function initAll(): void {
    document.querySelectorAll<HTMLSelectElement>('select[data-tom-select]').forEach(function (sel: HTMLSelectElement) {
      if ((sel as unknown as Record<string, unknown>).tomselect) return;
      initTomSelect(sel);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  // Re-init after HTMX content swaps
  document.body.addEventListener('htmx:afterSettle', function () {
    initAll();
  });

  // Clean up Tom Select instances before elements are removed from the DOM
  document.body.addEventListener('htmx:beforeCleanUp', function (evt: Event) {
    const target = (evt as CustomEvent).detail.target as HTMLElement;
    if (!target) return;
    target.querySelectorAll<HTMLSelectElement>('select[data-tom-select]').forEach(function (sel) {
      const ts = (sel as any).tomselect;
      if (ts && typeof ts.destroy === 'function') {
        try {
          ts.destroy();
        } catch (_e) {
          // Ignore
        }
      }
    });
  });
})();
