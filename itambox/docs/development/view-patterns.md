# View patterns

ITAMbox has four approved shapes for a UI view. Pick the one that matches what
the request *does*; do not invent a fifth, and do not reach for a function-based
view because a class feels heavy.

All of them live on the shared foundations in `itambox/views/generic/`:

| Component | Module | Responsibility |
|---|---|---|
| `PermissionResolver` | `generic/authorization.py` | The single implementation of the permission rules — codename format, fail-closed normalisation, and the 404-over-403 tenant policy. |
| `SecuredObjectActionMixin` | `generic/authorization.py` | The secured object-action base: fail-closed gate, tenant-scoped queryset, request-cached `get_object()`. |
| `RelatedObjectProvider` | `generic/related_objects.py` | The detail page's "Related Objects" sidebar (batched, tenant-scoped counts). |
| `TableContextBuilder` | `generic/table_context.py` | Table class resolution, request binding, and the column-config key. |
| HTMX response helpers | `generic/htmx_responses.py` | `success_response()` / `error_response()` / `trigger_response()` / `is_htmx_request()`. |

## 1. Standard CRUD — `ObjectListView` / `ObjectDetailView` / `ObjectEditView` / `ObjectDeleteView` / `ObjectCloneView`

**Use when** the request is "show me these rows", "show me this row", or "create,
change or delete one row through a `ModelForm`".

```python
class WidgetListView(ObjectListView):
    queryset = Widget.objects.all()
    filterset = WidgetFilterSet
    filterset_form = WidgetFilterForm
    table = WidgetTable
    action_buttons = ("add",)
```

You declare data (`queryset`, `filterset`, `table`, detail `Panel`s); the base
derives everything else. In particular **do not** hand-write
`get_permission_required()` — the CRUD bases already resolve
`<app>.<action>_<model>` from the view's model via `PermissionResolver`, and a
view that cannot resolve its model denies rather than opening up.

Authorization is object-scoped: the check runs with `obj=` so the tenant
membership backend can anchor it at the row's own tenant.

## 2. Form-backed transaction — `GenericTransactionView`

**Use when** the action needs user input (a form) *and* runs a domain service
inside one transaction: checkout, check-in, approve-with-notes, close-with-reason.

```python
class WidgetCheckoutView(GenericTransactionView):
    permission_required = ("inventory.change_widget",)
    queryset = Widget.objects.all()
    model_form = WidgetCheckoutForm
    service_callable = checkout_widget
    success_message = _("Widget checked out.")
```

The base wraps `service_callable` in `transaction.atomic()`, maps cleaned form
data to service kwargs (`form_field_map` / `form_exclude_fields`), converts a
`ValidationError` into form errors, and answers HTMX with `204 + HX-Trigger`.
Put the business logic in `<app>/services.py`, not in the view.

Hooks worth knowing: `post_service()` runs inside the same transaction;
`hx_redirect_on_success` swaps the toast for a full navigation; `error_partial`
re-renders just the form fragment with `422` on validation failure.

## 3. POST-only action — `SimplePostView`

**Use when** the action takes no user input beyond "do it": acknowledge an alert,
start an audit session, mark a request fulfilled, transition a purchase order.

```python
class WidgetRetireView(SimplePostView):
    permission_required = ("inventory.change_widget",)
    queryset = Widget.objects.all()

    def perform_action(self, obj, request):
        retire_widget(obj, user=request.user)
        return {"message": _("Widget retired.")}
```

`perform_action()` returns a dict; `message` becomes the toast. Raise
`ValidationError` for a business-rule failure (danger toast for HTMX, message +
redirect otherwise) and `PermissionDenied` for an authorization failure.

There is no GET handler by design — an action that mutates state must not be
reachable by navigation.

## 4. Explicit self-service authorization opt-out — `permission_required = ()`

**Use only when** the rule genuinely cannot be expressed as a static permission
because it depends on the object's relationship to the caller — "the requester
may cancel their own request", "the assigned user may claim their own asset".

```python
class RequestCancelView(SimplePostView):
    # Self-authorizing: the requester may cancel their own request; everyone else
    # needs staff/approve_assetrequest. The per-object ownership check lives in
    # perform_action, so opt out of the static permission gate (fail-closed base).
    permission_required = ()
    queryset = AssetRequest.objects.all()

    def perform_action(self, obj, request):
        if obj.requester != request.user and not (
            request.user.is_staff or request.user.has_perm("assets.approve_assetrequest")
        ):
            raise PermissionDenied(_("You do not have permission to cancel this request."))
        ...
```

Rules for the opt-out:

1. **`()` is the only way to opt out.** Leaving `permission_required` unset
   (`None`) raises `ImproperlyConfigured` — the bases fail closed. This is
   deliberate: a missing declaration once meant *any* authenticated tenant member
   could run the action.
2. **`()` is not "no authorization".** It moves the check into
   `perform_action()`/`form_valid()`, which must raise `PermissionDenied` on
   failure. A `()` view with no check in its body is a security bug.
3. **Comment why**, naming the ownership rule and the staff/permission fallback,
   as above.
4. **Tenant scoping still applies.** The queryset is narrowed to the active
   tenant regardless, so the opt-out never widens the tenant boundary.

## Why the tenant boundary answers 404

Every object-scoped base resolves the object through
`PermissionResolver.object_under_check()`. When the row is outside the active
tenant the queryset raises `Http404`, and for an authenticated user that 404 is
re-raised rather than converted into a 403 — a 403 would confirm the pk exists in
another tenant. Anonymous users fall through to the login redirect.

## Function-based views

Existing FBVs are not being converted wholesale. Write a new one only for
endpoints that are not object actions at all (health checks, file streaming,
redirect shims). Anything that loads one object, checks a permission and mutates
it belongs in pattern 2 or 3 — those bases already carry the tenant scoping,
fail-closed gate and HTMX contract that a hand-rolled FBV has to re-derive
(and historically got wrong).

## Testing expectations

A new view needs coverage for: tenant scoping (a foreign-tenant pk answers 404),
permissions (the declarative gate denies; for a `()` view, the in-body check
denies), and both HTMX and non-HTMX response shapes. See
`itambox/tests/test_generic_view_foundations.py` for the reference cases and
[Security Test Expectations](security-test-expectations.md) for what boundary
tests must assert.
