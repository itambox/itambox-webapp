from core.forms import FilterForm
from compliance.filters import AuditSessionFilterSet


class AuditSessionFilterForm(FilterForm):
    filterset_class = AuditSessionFilterSet
