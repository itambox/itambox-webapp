"""Shared authorization foundations for the generic view layer.

Every generic view in this package answers the same three questions — *which*
permission does this view require, *which* object should the check be anchored
at, and *what* happens when that object is outside the active tenant. Before
issue #82 each view answered them with its own near-identical copy of the same
fifteen lines, which is exactly the shape of duplication that lets a security
fix land in four places and get missed in the fifth.

``PermissionResolver`` is that single implementation. ``SecuredObjectActionMixin``
layers the object-action plumbing (tenant-scoped queryset, cached lookup,
fail-closed permission gate) on top of it for the two service-view bases.
"""

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.shortcuts import get_object_or_404


class PermissionResolver:
    """The one implementation of ITAMbox's view-authorization rules.

    Stateless by design: every method takes the view (or model) it operates on,
    so the same logic serves ``PermissionRequiredMixin`` subclasses, the service
    views, and any future caller without dragging an inheritance chain along.
    """

    @staticmethod
    def permission_codename(model, action):
        """``"<app_label>.<action>_<model_name>"`` — the repo-wide permission format."""
        return f"{model._meta.app_label}.{action}_{model._meta.model_name}"

    @classmethod
    def model_permissions(cls, model, action):
        """The ``get_permission_required()`` tuple for ``action`` on ``model``.

        An unresolvable model yields ``("",)`` rather than ``()``. That
        distinction is load-bearing: ``()`` reads as "this view requires no
        permission", while ``("",)`` is a non-empty tuple holding a codename no
        backend can grant — so a view that cannot work out its own model denies
        instead of opening up.
        """
        if model is None:
            return ("",)
        return (cls.permission_codename(model, action),)

    @staticmethod
    def declared_permissions(view):
        """Normalise a view's declared ``permission_required``, failing closed.

        A missing (``None``) ``permission_required`` on a view that mutates state
        is a developer error, not an open door — historically it silently allowed
        ANY authenticated tenant member to run the action (B3). Views that
        intentionally perform their own per-object authorization (e.g. an
        ownership check inside ``perform_action``/``form_valid``) opt out
        explicitly by setting ``permission_required = ()``.
        """
        declared = view.permission_required
        if declared is None:
            raise ImproperlyConfigured(
                f"{view.__class__.__name__} is missing permission_required. Set it "
                f"to the required permission(s), or to an empty tuple () to opt into "
                f"handling authorization itself."
            )
        if isinstance(declared, str):
            return (declared,)
        return declared

    @staticmethod
    def object_under_check(view):
        """The object an object-scoped permission check should be anchored at.

        Answers 404 (not 403) for objects outside the tenant scope: don't reveal
        whether the pk exists in another tenant. Anonymous users fall through to
        the permission check (and the login redirect) with ``None``.
        """
        try:
            return view.get_object()
        except Http404:
            if view.request.user.is_authenticated:
                raise
            return None

    @classmethod
    def has_object_permission(cls, view, perms):
        """Run ``perms`` against the view's object.

        An empty ``perms`` means the view opted into authorizing itself, so the
        object is never even fetched.
        """
        if not perms:
            return True
        return view.request.user.has_perms(perms, obj=cls.object_under_check(view))


class SecuredObjectActionMixin:
    """Shared base for object-scoped action views (one object, one mutation).

    Supplies the four things every action view needs and no action view should
    re-implement:

    * ``get_permission_required`` — fail-closed normalisation of the declared
      permission (see :meth:`PermissionResolver.declared_permissions`);
    * ``has_permission`` — the object-anchored check with the 404-over-403
      tenant-boundary policy;
    * ``get_queryset`` — the declared queryset narrowed to the active tenant;
    * ``get_object`` — a request-lifetime cached lookup, because the object is
      needed by ``has_permission``, the form kwargs, the action itself and the
      template context within a single request.

    Subclasses keep every existing extension point: overriding ``has_permission``
    (to widen a check to a recipient tenant, say) or ``get_queryset`` continues
    to work exactly as before.
    """

    queryset = None

    #: Raise ``NotImplementedError`` when the URL supplies no ``pk`` instead of
    #: running a lookup that can only 404. ``SimplePostView`` sets this True — it
    #: has no form or object fallback, so a pk-less route is a wiring bug.
    #: ``GenericTransactionView`` leaves it False to preserve its historical
    #: lookup-anyway behaviour.
    strict_pk_required = False

    def get_permission_required(self):
        return PermissionResolver.declared_permissions(self)

    def has_permission(self):
        return PermissionResolver.has_object_permission(self, self.get_permission_required())

    def get_queryset(self):
        if self.queryset is None:
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} is missing a QuerySet. Define {self.__class__.__name__}.queryset."
            )
        queryset = self.queryset.all()
        if hasattr(queryset, "filter_by_tenant"):
            queryset = queryset.filter_by_tenant()
        return queryset

    def get_object(self):
        if getattr(self, "_cached_object", None) is not None:
            return self._cached_object
        pk = self.kwargs.get("pk")
        if pk is None and self.strict_pk_required:
            raise NotImplementedError(f"{self.__class__.__name__} must define 'queryset' or override get_object()")
        self._cached_object = get_object_or_404(self.get_queryset(), pk=pk)
        return self._cached_object
