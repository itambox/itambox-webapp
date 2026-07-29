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

from itambox.capabilities import registry

#: The specification extension key. ``x-`` prefixed, so it validates as a
#: vendor extension anywhere an OpenAPI operation object is allowed.
MATURITY_EXTENSION = "x-itambox-maturity"


class CapabilityAwareAutoSchema(AutoSchema):
    """Annotates every operation whose model the capability registry owns."""

    def get_operation(self, *args, **kwargs):
        operation = super().get_operation(*args, **kwargs)
        if operation is not None:
            self.annotate_capability_maturity(operation)
        return operation

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
