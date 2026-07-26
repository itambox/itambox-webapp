"""Table construction for list pages.

``ObjectListView`` resolves a django-tables2 table class (explicit attribute
first, then the per-model registry), binds it to the request, and publishes a
stable config key the column-configuration UI stores preferences under. That
sequence is extracted here so detail-view tabs and any future list-like surface
can reuse it instead of re-deriving the key format.
"""

from django.http import Http404

from itambox.utils import get_table_for_model


class TableContextBuilder:
    """Resolve and instantiate the table for ``model``.

    ``table_class`` is the view's declared ``table`` attribute when it has one;
    otherwise the model's registered table is looked up.
    """

    def __init__(self, model, table_class=None):
        self.model = model
        self.table_class = table_class

    def resolve_table_class(self):
        table_class = self.table_class or get_table_for_model(self.model)
        if not table_class:
            raise Http404(f"No table defined for model {self.model._meta.model_name}")
        return table_class

    def build(self, data, request):
        """Instantiate the table bound to ``request``.

        Deliberately does NOT call ``table.configure()`` — the list view applies
        that later, after ``get_table()`` has given subclasses their chance to
        substitute or decorate the table.
        """
        return self.resolve_table_class()(data, request=request)

    @staticmethod
    def config_key(model, table):
        """The key column preferences are stored under for this list."""
        return f"{model._meta.app_label}.{table.__class__.__name__}"
