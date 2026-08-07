"""Capability maturity as an OpenAPI specification extension.

An API client deserves the same warning a UI user gets. A generated client for
``/api/extras/webhook-endpoints/`` should be able to see that the payload it is
binding to is Beta, without reading the README.

``x-itambox-maturity`` carries that. It is emitted only for operations whose
model a capability actually owns, so its *absence* means "no capability claims
this endpoint" rather than "this endpoint is fine" -- an unowned endpoint and a
Stable one are genuinely different statements and the schema keeps them apart.

The grade published here is the declared contract, never the activation state:
a deployment that has switched a capability off still serves the same schema,
because the schema describes the software, not the deployment.
"""

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny

from itambox.capabilities import registry

#: The specification extension key. ``x-`` prefixed, so it validates as a
#: vendor extension anywhere an OpenAPI operation object is allowed.
MATURITY_EXTENSION = "x-itambox-maturity"


@extend_schema_serializer(component_name="APIError")
class APIErrorSerializer(serializers.Serializer):
    """Stable envelope for validation and authorization failures."""

    detail = serializers.CharField()
    code = serializers.CharField(required=False)
    fieldErrors = serializers.DictField(required=False, child=serializers.ListField(child=serializers.CharField()))


class CapabilityAwareAutoSchema(AutoSchema):
    """Annotates every operation whose model the capability registry owns."""

    def get_operation(self, *args, **kwargs):
        operation = super().get_operation(*args, **kwargs)
        if operation is not None:
            self.annotate_capability_maturity(operation)
            self.add_error_responses(operation)
        return operation

    def get_operation_id(self):
        """Keep collection mutations distinct from item operations.

        drf-spectacular's default IDs are deterministic, but custom list routes
        that mutate a collection otherwise share the same semantic namespace as
        item updates in several client generators. The suffix is deliberately
        limited to collection mutations so ordinary CRUD IDs remain unchanged.
        """
        operation_id = super().get_operation_id()
        if self.method in {"DELETE", "PATCH", "PUT"} and "{" not in self.path and not operation_id.endswith("_bulk"):
            return f"{operation_id}_bulk"
        return operation_id

    def add_error_responses(self, operation):
        """Publish common REST errors without overriding explicit responses."""
        error_component = self.resolve_serializer(APIErrorSerializer(), direction="response")
        if not error_component:
            return operation

        status_codes = {"400"}
        if "{" in self.path:
            status_codes.add("404")
        if self.method in {"POST", "PUT", "PATCH"}:
            status_codes.add("409")
            if "/scim/" not in self.path:
                status_codes.update({"412", "428"})
        elif self.method == "DELETE" and "/scim/" not in self.path:
            status_codes.update({"412", "428"})

        view = getattr(self, "_view", None)
        permission_classes = getattr(view, "permission_classes", ())
        if permission_classes and not any(self._is_allow_any(permission) for permission in permission_classes):
            status_codes.update({"401", "403"})

        for status_code in sorted(status_codes):
            operation.setdefault("responses", {}).setdefault(
                status_code,
                {
                    "description": "The request could not be completed.",
                    "content": {"application/json": {"schema": error_component.ref}},
                },
            )
        return operation

    @staticmethod
    def _is_allow_any(permission):
        try:
            return issubclass(permission, AllowAny)
        except TypeError:
            return False

    def annotate_capability_maturity(self, operation):
        """Add the declared grade to ``operation``, or leave it untouched."""
        maturity = self.capability_maturity()
        if maturity is not None:
            operation[MATURITY_EXTENSION] = maturity
        return operation

    def capability_maturity(self):
        """The grade declared for this view's model or implementation module.

        Model ownership is authoritative for ordinary ViewSets. APIViews such
        as SCIM do not expose ``queryset.model``; for those, a capability may
        own the view's module subtree. Resolution is total and silent: an
        unowned view simply publishes no extension.
        """
        model = self._resolve_model()
        meta = getattr(model, "_meta", None)
        capability = None
        if meta is not None:
            capability = registry.owner_of(f"{meta.app_label}.{model.__name__}")
        if capability is None:
            view = getattr(self, "_view", None)
            capability = registry.owner_of_module(getattr(type(view), "__module__", ""))
        return None if capability is None else capability.maturity

    def _resolve_model(self):
        view = getattr(self, "_view", None)
        if view is None:
            return None
        queryset = getattr(view, "queryset", None)
        return getattr(queryset, "model", None)
