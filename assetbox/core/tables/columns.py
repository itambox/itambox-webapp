# assetbox/core/tables/columns.py
import django_tables2 as tables
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from ..utils import get_model_viewname # Import the utility function

class ActionsColumn(tables.Column):
    """
    Renders a dropdown menu with edit and delete links for an object.
    Derives URL names based on the model.
    """
    attrs = {'td': {'class': 'text-end text-nowrap noprint'}} # Mimic NetBox attrs
    empty_values = ()
    verbose_name = '' # No header text
    orderable = False

    def render(self, record, table, **kwargs):
        # Ensure we have a record with a pk
        if not record or not getattr(record, 'pk', None):
            return self.default

        # Get URLs dynamically
        model = type(record)
        try:
            url_edit = reverse(get_model_viewname(model, 'update'), kwargs={'pk': record.pk})
        except:
            url_edit = None # Handle case where update view doesn't exist

        try:
            url_delete = reverse(get_model_viewname(model, 'delete'), kwargs={'pk': record.pk})
        except:
            url_delete = None # Handle case where delete view doesn't exist

        # Add other URLs like changelog later if needed

        # Use Tabler icons SVG markup
        icon_edit = '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-pencil" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M4 20h4l10.5 -10.5a1.5 1.5 0 0 0 -4 -4l-10.5 10.5v4" /><path d="M13.5 6.5l4 4" /></svg>'
        icon_delete = '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-trash" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M4 7l16 0" /><path d="M10 11l0 6" /><path d="M14 11l0 6" /><path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12" /><path d="M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3" /></svg>'

        # Build the split button dropdown HTML
        html_parts = [
            f'''<div class="btn-group dropdown">'''
        ]

        # Edit button (if URL exists)
        if url_edit:
            html_parts.append(f'''<a href="{url_edit}" class="btn btn-sm btn-primary" title="{_('Edit')}">{icon_edit}</a>''')

        # Only show toggle and menu if there are other actions (delete for now)
        if url_delete: # Add conditions for other actions here later
            # Use primary color for toggle if edit exists, else secondary
            toggle_color = 'primary' if url_edit else 'secondary'
            html_parts.append(f'''<button class="btn btn-sm btn-{toggle_color} dropdown-toggle dropdown-toggle-split" type="button" data-bs-toggle="dropdown" aria-expanded="false">
                                   <span class="visually-hidden">{_('Toggle Dropdown')}</span>
                               </button>
                               <ul class="dropdown-menu dropdown-menu-end" data-bs-strategy="fixed">''')
            if url_delete:
                html_parts.append(f'''<li><a class="dropdown-item text-danger" href="{url_delete}">{icon_delete} {_("Delete")}</a></li>''')
            # Add other actions here
            html_parts.append('''   </ul>''')

        html_parts.append('''</div>''')

        # Don't render empty div if no actions are available
        if not url_edit and not url_delete:
            return self.default

        return mark_safe("\n".join(html_parts)) 