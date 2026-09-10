"""Guard branches of the access-scope DTOs and the pure resolution helpers."""

from django.test import SimpleTestCase

from organization.services.access_scope import (
    AccessScopeDeniedDTO,
    AccessScopeDTO,
    AccessScopeResolutionRequestDTO,
    AccessScopeResolvedDTO,
    ActorContextDTO,
    RequestedScopeSelectorDTO,
    ResolvedAccessAuthorizationDTO,
    _authorized_tenant_ids,
    _validate_provider_sets,
)


def _actor(actor_id=1, revision="rev-a"):
    return ActorContextDTO(actor_id=actor_id, authentication_revision=revision)


def _selector(mode="all_accessible"):
    return RequestedScopeSelectorDTO(mode=mode, tenant_id=None, tenant_group_id=None)


def _request(operation="read_asset", permission="assets.read"):
    return AccessScopeResolutionRequestDTO(
        actor=_actor(),
        selector=_selector(),
        operation=operation,
        required_permission=permission,
    )


class ActorContextGuardTests(SimpleTestCase):
    def test_rejects_non_positive_actor_id(self):
        with self.assertRaises(ValueError):
            ActorContextDTO(actor_id=0, authentication_revision="rev-a")

    def test_rejects_blank_revision(self):
        for bad in ("", 5, None):
            with self.assertRaises(ValueError):
                ActorContextDTO(actor_id=1, authentication_revision=bad)


class SelectorGuardTests(SimpleTestCase):
    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            RequestedScopeSelectorDTO(mode="bogus", tenant_id=None, tenant_group_id=None)

    def test_rejects_non_positive_ids(self):
        with self.assertRaises(ValueError):
            RequestedScopeSelectorDTO(mode="tenant", tenant_id=0, tenant_group_id=None)
        with self.assertRaises(ValueError):
            RequestedScopeSelectorDTO(mode="tenant_group", tenant_id=None, tenant_group_id=-1)

    def test_rejects_mismatched_selector_ids(self):
        with self.assertRaises(ValueError):
            RequestedScopeSelectorDTO(mode="tenant", tenant_id=None, tenant_group_id=None)
        with self.assertRaises(ValueError):
            RequestedScopeSelectorDTO(mode="tenant", tenant_id=1, tenant_group_id=2)
        with self.assertRaises(ValueError):
            RequestedScopeSelectorDTO(mode="all_accessible", tenant_id=1, tenant_group_id=None)

    def test_accepts_consistent_selectors(self):
        RequestedScopeSelectorDTO(mode="tenant", tenant_id=7, tenant_group_id=None)
        RequestedScopeSelectorDTO(mode="tenant_group", tenant_id=None, tenant_group_id=3)
        RequestedScopeSelectorDTO(mode="all_accessible", tenant_id=None, tenant_group_id=None)


class ResolutionRequestGuardTests(SimpleTestCase):
    def test_rejects_wrong_types(self):
        with self.assertRaises(TypeError):
            AccessScopeResolutionRequestDTO(
                actor=object(), selector=_selector(), operation="read_asset", required_permission="assets.read"
            )
        with self.assertRaises(TypeError):
            AccessScopeResolutionRequestDTO(
                actor=_actor(), selector=object(), operation="read_asset", required_permission="assets.read"
            )

    def test_rejects_unknown_operation(self):
        with self.assertRaises(ValueError):
            AccessScopeResolutionRequestDTO(
                actor=_actor(), selector=_selector(), operation="bogus", required_permission="assets.read"
            )

    def test_rejects_blank_permission(self):
        for bad in ("", "   ", 5):
            with self.assertRaises(ValueError):
                AccessScopeResolutionRequestDTO(
                    actor=_actor(), selector=_selector(), operation="read_asset", required_permission=bad
                )


class AccessScopeGuardTests(SimpleTestCase):
    def _scope(self, **overrides):
        fields = dict(
            mode="tenant",
            authorized_tenant_ids=frozenset({1}),
            selector_fingerprint="fp",
            authorization_revision="rev",
            access_scope_fingerprint="afp",
            valid_until_epoch_seconds=None,
        )
        fields.update(overrides)
        return AccessScopeDTO(**fields)

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            self._scope(mode="bogus")

    def test_rejects_empty_or_wrong_tenant_set(self):
        with self.assertRaises(ValueError):
            self._scope(authorized_tenant_ids=frozenset())
        with self.assertRaises(ValueError):
            self._scope(authorized_tenant_ids=[1])
        with self.assertRaises(ValueError):
            self._scope(authorized_tenant_ids=frozenset({0}))

    def test_rejects_blank_fingerprints(self):
        for name in ("selector_fingerprint", "authorization_revision", "access_scope_fingerprint"):
            with self.assertRaises(ValueError):
                self._scope(**{name: ""})
            with self.assertRaises(ValueError):
                self._scope(**{name: 5})

    def test_valid_until_guards(self):
        with self.assertRaises(TypeError):
            self._scope(valid_until_epoch_seconds="tomorrow")
        with self.assertRaises(TypeError):
            self._scope(valid_until_epoch_seconds=True)
        with self.assertRaises(ValueError):
            self._scope(valid_until_epoch_seconds=-1)
        self._scope(valid_until_epoch_seconds=0)
        self._scope(valid_until_epoch_seconds=123456)


class ResolutionResultGuardTests(SimpleTestCase):
    def test_resolved_outcome_must_match(self):
        with self.assertRaises(ValueError):
            AccessScopeResolvedDTO(
                outcome="denied",  # type: ignore[arg-type]
                request=_request(),
                access_scope=AccessScopeDTO(
                    mode="tenant",
                    authorized_tenant_ids=frozenset({1}),
                    selector_fingerprint="fp",
                    authorization_revision="rev",
                    access_scope_fingerprint="afp",
                    valid_until_epoch_seconds=None,
                ),
            )

    def test_denied_outcome_must_match(self):
        with self.assertRaises(ValueError):
            AccessScopeDeniedDTO(outcome="resolved", public_code="OBJECT_UNAVAILABLE", public_path=())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            AccessScopeDeniedDTO(outcome="denied", public_code="NOT_FOUND", public_path=())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            AccessScopeDeniedDTO(outcome="denied", public_code="OBJECT_UNAVAILABLE", public_path=["a"])  # type: ignore[arg-type]

    def test_authorization_actor_must_match_request(self):
        scope = AccessScopeDTO(
            mode="tenant",
            authorized_tenant_ids=frozenset({1}),
            selector_fingerprint="fp",
            authorization_revision="rev",
            access_scope_fingerprint="afp",
            valid_until_epoch_seconds=None,
        )
        with self.assertRaises(ValueError):
            ResolvedAccessAuthorizationDTO(actor=_actor(2), request=_request(), initial_scope=scope)


class PureResolutionHelperTests(SimpleTestCase):
    def test_validate_provider_sets_rejects_foreign_ids(self):
        with self.assertRaises(ValueError):
            _validate_provider_sets({1, 99}, {}, {1, 2})
        with self.assertRaises(ValueError):
            _validate_provider_sets({1}, {2: (frozenset(), None)}, {1, 2})
        _validate_provider_sets({1}, {1: (frozenset(), None)}, {1, 2})

    def test_authorized_tenant_ids_selection(self):
        permission_map = {
            1: (frozenset({"assets.read"}), None),
            2: (frozenset({"assets.write"}), None),
        }
        live = {1, 2}
        # all_accessible returns every tenant that holds the permission
        self.assertEqual(
            _authorized_tenant_ids(_selector("all_accessible"), "assets.read", permission_map, live),
            {1},
        )
        # tenant mode only authorizes tenants within the permission set
        tenant_selector = RequestedScopeSelectorDTO(mode="tenant", tenant_id=2, tenant_group_id=None)
        self.assertIsNone(_authorized_tenant_ids(tenant_selector, "assets.read", permission_map, live))
        self.assertEqual(
            _authorized_tenant_ids(tenant_selector, "assets.write", permission_map, live),
            {2},
        )
        # empty permission set denies
        self.assertIsNone(_authorized_tenant_ids(_selector("all_accessible"), "assets.read", {}, live))
