"""Test-only builders for deliberately corrupt legacy assignment rows."""

from django.db import connection


def force_assignment_fixture_update(assignment, **values):
    """Bypass model guards solely to exercise integrity-report detection."""

    model = type(assignment)
    assignments = []
    parameters = []
    for name, value in values.items():
        field = model._meta.get_field(name)
        assignments.append(f"{connection.ops.quote_name(field.column)} = %s")
        parameters.append(getattr(value, "pk", value))
    parameters.append(assignment.pk)
    table = connection.ops.quote_name(model._meta.db_table)
    pk_column = connection.ops.quote_name(model._meta.pk.column)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {table} SET {', '.join(assignments)} WHERE {pk_column} = %s", parameters)
