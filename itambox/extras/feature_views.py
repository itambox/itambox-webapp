"""Extras-owned generic-presentation provider."""

from itambox.registry import (
    DetailContextInput,
    ListContextInput,
    ListFilterInput,
    ListParamsInput,
    ListParamsResult,
)


class _ExtrasGenericPresentationProvider:
    def resolve_list_params(self, input: ListParamsInput) -> ListParamsResult:
        return ListParamsResult(params=input.params, state={})

    def filter_list_queryset(self, input: ListFilterInput):
        return input.queryset

    def build_list_context(self, input: ListContextInput) -> dict[str, object]:
        return {}

    def build_detail_context(self, input: DetailContextInput) -> dict[str, object]:
        return {}


EXTRAS_GENERIC_PRESENTATION_PROVIDER = _ExtrasGenericPresentationProvider()
