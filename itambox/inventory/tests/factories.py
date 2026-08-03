"""Explicit test-fixture builders for service-protected assignment models."""

from inventory.models_assignment_write import authorized_assignment_write


def create_assignment_fixture(model, **kwargs):
    assignment = model(**kwargs)
    with authorized_assignment_write(assignment):
        assignment.save()
    return assignment
