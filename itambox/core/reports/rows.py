"""Row assembly shared by the domain report providers.

A provider declares *what* each of its columns renders and *how* its rows
group; the helpers here own the mechanics every report shares — selecting the
template's columns, keying cells by their resolved header label, and appending
the grouping key the common orchestration reads back.
"""

from .columns import label_for

GROUP_FIELD = "_group_by"
DEFAULT_GROUP = "General"


def report_row(cells, columns, record, request, group_key):
    """Render one data row from a provider's declared cell renderers.

    ``cells`` maps a column key to ``renderer(record, request)`` and its order
    is the row's order — a template listing its columns in another order
    changes the header order, never the row's. A renderer is not called for a
    column the template did not select, so an expensive cell (a related lookup,
    a derived lifetime) costs nothing when it is not shown.
    """
    row = {label_for(column): render(record, request) for column, render in cells.items() if column in columns}
    row[GROUP_FIELD] = group_key
    return row


def sample_report_row(sample_cells, columns, group_key):
    """Render the illustrative row shown when a report's scope holds no data.

    Ordered by the template's own column list — the sample row is written for
    the report designer's preview, where the author is looking at the columns
    in the order they picked them.
    """
    row = {label_for(column): sample_cells[column] for column in columns if column in sample_cells}
    row[GROUP_FIELD] = group_key
    return row


def group_key_for(group_by_field, resolvers, record, request, default=DEFAULT_GROUP):
    """Resolve a row's grouping key, falling back for an unsupported field."""
    resolver = resolvers.get(group_by_field)
    if resolver is None:
        return default
    return resolver(record, request)


def sample_group_key_for(group_by_field, sample_keys, default=DEFAULT_GROUP):
    """The grouping key the sample row carries for the selected group field."""
    return sample_keys.get(group_by_field, default)
