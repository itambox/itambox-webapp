from compliance.filters import AuditSessionFilterSet
from core.forms import FilterForm


class AuditSessionFilterForm(FilterForm):
    filterset_class = AuditSessionFilterSet
